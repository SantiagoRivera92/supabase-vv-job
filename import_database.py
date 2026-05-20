import json
import os
import requests
import time
from dotenv import load_dotenv
from supabase import create_client, Client
from typing import Dict, List, Optional, Callable, Any, TypedDict, Union
import httpx


# Type definitions
class CardInfo(TypedDict):
    name: str
    oracle_id: Optional[str]


class MoxfieldCard(TypedDict):
    quantity: int
    card: CardInfo


class MoxfieldBoard(TypedDict):
    cards: Dict[str, MoxfieldCard]


class MoxfieldBoards(TypedDict):
    mainboard: MoxfieldBoard
    sideboard: MoxfieldBoard


class MoxfieldUser(TypedDict):
    userName: Optional[str]


class MoxfieldDeckData(TypedDict, total=False):
    boards: Optional[MoxfieldBoards]
    createdByUser: Optional[MoxfieldUser]
    originalDeck: Optional[Dict[str, Any]]


class PlayerRecord(TypedDict):
    p_id: int
    name: str
    deck_link: Optional[str]
    deck_name: Optional[str]
    t_id: str
    archived_at: Optional[str]


class PairingRecord(TypedDict):
    p_id: int
    opponent_id: int
    round: int
    wins: Optional[int]
    losses: Optional[int]
    t_id: str
    archived_at: Optional[str]


class TournamentRecord(TypedDict):
    id: str
    t_name: str
    archived_at: Optional[str]


class ArchiveData(TypedDict):
    tournaments: List[TournamentRecord]
    players: List[PlayerRecord]
    pairings: List[PairingRecord]


class DeckInfo(TypedDict):
    id: str
    archetype_id: str
    cards: List[str]


class SimilarArchetypeRow(TypedDict):
    out_archetype_name: str


# Use Union for Supabase API responses since we can't import the exact types
SupabaseResult = Union[Any, None]


# Load environment variables
load_dotenv()

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    raise ValueError(
        "SUPABASE_URL and SUPABASE_KEY must be set in environment variables."
    )

# Initialize Supabase client
supabase: Client = create_client(supabase_url, supabase_key)

MOXFIELD_HEADERS = {"User-Agent": "MoxKey; DDT 46c2c16aba20"}


def fetch_deck_from_moxfield(deck_url: str) -> Optional[MoxfieldDeckData]:
    """Fetch deck data from Moxfield API."""
    # Extract deck ID from URL
    deck_id = deck_url.split("/")[-1]
    api_url = f"https://api2.moxfield.com/v3/decks/all/{deck_id}"

    try:
        response = requests.get(api_url, headers=MOXFIELD_HEADERS)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error fetching deck {deck_url}: {e}")
        return None


def execute_with_retry(
    func: Callable[[], Any], max_retries: int = 3, delay: int = 1
) -> Any:
    """Execute a function with retry logic for connection errors."""
    for attempt in range(max_retries):
        try:
            return func()
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
            if attempt < max_retries - 1:
                print(f"Connection error: {e}. Retrying in {delay} second(s)...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                print(f"Failed after {max_retries} attempts.")
                raise


def get_or_create_card(card_name: str) -> Optional[str]:
    """Get card oracle_id from database by name, or return None if not found."""
    result = execute_with_retry(
        lambda: supabase.table("cards")
        .select("oracle_id")
        .eq("name", card_name)
        .execute()
    )
    if not result:
        return None
    data = result.data
    if data and len(data) > 0:
        first = data[0]
        if "oracle_id" in first and isinstance(first["oracle_id"], str):
            return first["oracle_id"]

    print(f"Warning: Card '{card_name}' not found in database. Skipping.")
    return None


def get_or_create_player(discord_id: int, username: str) -> int:
    """Ensure player exists in database."""
    # Check if player exists
    result = execute_with_retry(
        lambda: supabase.table("players")
        .select("discord_id")
        .eq("discord_id", discord_id)
        .execute()
    )

    if result.data and len(result.data) > 0:
        # Player exists, return their ID
        return discord_id
    else:
        # Create new player
        execute_with_retry(
            lambda: supabase.table("players")
            .insert({"discord_id": discord_id, "username": username})
            .execute()
        )
        return discord_id


def ensure_bye_player():
    """Ensure the Bye player exists in the database."""
    get_or_create_player(0, "Bye")


def get_archetype_id(archetype_name: str) -> str:
    """Get or create archetype and return its ID."""
    # Check if archetype exists
    result = execute_with_retry(
        lambda: supabase.table("archetypes")
        .select("id")
        .eq("name", archetype_name)
        .execute()
    )

    if result.data and len(result.data) > 0:
        return result.data[0]["id"]
    else:
        # Create archetype
        new_archetype = execute_with_retry(
            lambda: supabase.table("archetypes")
            .insert({"name": archetype_name})
            .execute()
        )

        archetype_id = new_archetype.data[0]["id"]

        return archetype_id


def find_similar_decks(
    deck_data: MoxfieldDeckData, similarity_threshold: float = 0.5
) -> List[str]:
    """
    Find decks with similar mainboard composition using a Supabase RPC function.
    Returns a list of archetype names ordered by similarity.
    """
    unique_oracle_ids = set()

    boards = deck_data.get("boards")
    if boards and "mainboard" in boards:
        for card_key, card_info in boards["mainboard"].get("cards", {}).items():
            card_name = card_info["card"]["name"]
            oracle_id = get_or_create_card(card_name)
            if oracle_id:
                unique_oracle_ids.add(oracle_id)

    if not unique_oracle_ids:
        print("No valid cards found in deck mainboard to compare.")
        return []

    try:
        response = execute_with_retry(
            lambda: supabase.rpc(
                "get_similar_archetypes",
                {
                    "target_oracle_ids": list(unique_oracle_ids),
                    "similarity_floor": similarity_threshold,
                },
            ).execute()
        )

        if not response.data:
            return []

        similar_archetypes = [row["out_archetype_name"] for row in response.data]

        return similar_archetypes

    except Exception as e:
        print(f"Error during RPC similarity check: {e}")
        return []


def display_decklist(deck_data: MoxfieldDeckData) -> None:
    """Display the deck's mainboard and sideboard in the console."""
    print("\n" + "=" * 60)
    print("MAINBOARD:")
    print("=" * 60)

    boards = deck_data.get("boards")
    if boards and "mainboard" in boards:
        cards = []
        for card_key, card_info in boards["mainboard"].get("cards", {}).items():
            cards.append((card_info["quantity"], card_info["card"]["name"]))

        # Sort by quantity descending, then alphabetically
        cards.sort(key=lambda x: (-x[0], x[1]))

        for quantity, name in cards:
            print(f"{quantity}x {name}")

    print("\n" + "=" * 60)
    print("SIDEBOARD:")
    print("=" * 60)

    if boards and "sideboard" in boards:
        cards = []
        for card_key, card_info in boards["sideboard"].get("cards", {}).items():
            cards.append((card_info["quantity"], card_info["card"]["name"]))

        # Sort by quantity descending, then alphabetically
        cards.sort(key=lambda x: (-x[0], x[1]))

        for quantity, name in cards:
            print(f"{quantity}x {name}")

    print("=" * 60 + "\n")


def prompt_for_archetype(
    deck_name: str, player_name: str, deck_url: str, deck_data: MoxfieldDeckData
) -> str:
    """Prompt user to input archetype name for a deck."""
    # Display the decklist
    display_decklist(deck_data)

    # Find similar decks
    similar_archetypes = find_similar_decks(deck_data)

    print(f"\nDeck URL: {deck_url}")
    print(f"Player: {player_name} | Deck: {deck_name}")

    if similar_archetypes:
        print("\nSimilar decks found:")
        print("0. Insert your own")
        for i, archetype in enumerate(similar_archetypes, 1):
            print(f"{i}. {archetype}")

        while True:
            try:
                choice = input("\nSelect archetype number: ").strip()
                choice_num = int(choice)

                if choice_num == 0:
                    archetype_name = input("Enter archetype name: ").strip()
                    return get_archetype_id(archetype_name)
                elif 1 <= choice_num <= len(similar_archetypes):
                    archetype_name = similar_archetypes[choice_num - 1]
                    print(f"Selected: {archetype_name}")
                    return get_archetype_id(archetype_name)
                else:
                    print(
                        f"Invalid choice. Please enter a number between 0 and {len(similar_archetypes)}"
                    )
            except ValueError:
                print("Invalid input. Please enter a number.")
    else:
        print("No similar decks found in database.")
        archetype_name = input("Enter archetype name: ").strip()
        return get_archetype_id(archetype_name)


def get_existing_deck(player_id: int, moxfield_link: str) -> Optional[str]:
    """Check if a deck already exists for this player and Moxfield link."""
    result = execute_with_retry(
        lambda: supabase.table("decks")
        .select("id")
        .eq("player_id", player_id)
        .eq("moxfield_link", moxfield_link)
        .execute()
    )

    if result.data and len(result.data) > 0:
        return result.data[0]["id"]
    return None


def create_deck(
    player_id: int, deck_url: str, deck_name: str, player_name: str
) -> Optional[DeckInfo]:
    """Create deck in database with cards from Moxfield. Returns deck info."""
    # Check if deck already exists
    existing_deck_id = get_existing_deck(player_id, deck_url)
    if existing_deck_id:
        print(f"Deck already exists for {player_name}: {deck_name} (using existing)")
        # Get deck's archetype and cards
        deck_info = execute_with_retry(
            lambda: supabase.table("decks")
            .select("id, archetype_id")
            .eq("id", existing_deck_id)
            .execute()
        )
        deck_cards = execute_with_retry(
            lambda: supabase.table("deck_cards")
            .select("oracle_id")
            .eq("deck_id", existing_deck_id)
            .execute()
        )
        return {
            "id": existing_deck_id,
            "archetype_id": deck_info.data[0]["archetype_id"],
            "cards": [c["oracle_id"] for c in deck_cards.data]
            if deck_cards.data
            else [],
        }

    # Fetch deck data
    deck_data = fetch_deck_from_moxfield(deck_url)
    if not deck_data:
        return None

    # Try to get username from originalDeck first (for bot-uploaded decks), then fall back to createdByUser
    moxfield_username = None
    original_deck = deck_data.get("originalDeck")
    if original_deck:
        moxfield_username = original_deck.get("createdByUser", {}).get("userName")

    if not moxfield_username:
        created_by_user = deck_data.get("createdByUser")
        if created_by_user:
            moxfield_username = created_by_user.get("userName")

    if moxfield_username:
        execute_with_retry(
            lambda: supabase.table("players")
            .update({"username": moxfield_username})
            .eq("discord_id", player_id)
            .execute()
        )
        player_name = moxfield_username
        print(f"Updated player name from Moxfield: {moxfield_username}")

    # Get archetype from user
    archetype_id = prompt_for_archetype(deck_name, player_name, deck_url, deck_data)

    # Create deck record
    deck_record = execute_with_retry(
        lambda: supabase.table("decks")
        .insert(
            {
                "player_id": player_id,
                "archetype_id": archetype_id,
                "moxfield_link": deck_url,
            }
        )
        .execute()
    )

    deck_id = deck_record.data[0]["id"]
    card_oracle_ids = []

    # Add mainboard cards
    boards = deck_data.get("boards")
    if boards and "mainboard" in boards:
        for card_key, card_info in boards["mainboard"].get("cards", {}).items():
            card_name = card_info["card"]["name"]
            quantity = card_info["quantity"]

            oracle_id = get_or_create_card(card_name)
            if oracle_id:
                execute_with_retry(
                    lambda: supabase.table("deck_cards")
                    .insert(
                        {
                            "deck_id": deck_id,
                            "oracle_id": oracle_id,
                            "quantity": quantity,
                            "location": "main",
                        }
                    )
                    .execute()
                )
                card_oracle_ids.append(oracle_id)

    # Add sideboard cards
    if boards and "sideboard" in boards:
        for card_key, card_info in boards["sideboard"].get("cards", {}).items():
            card_name = card_info["card"]["name"]
            quantity = card_info["quantity"]

            oracle_id = get_or_create_card(card_name)
            if oracle_id:
                execute_with_retry(
                    lambda: supabase.table("deck_cards")
                    .insert(
                        {
                            "deck_id": deck_id,
                            "oracle_id": oracle_id,
                            "quantity": quantity,
                            "location": "side",
                        }
                    )
                    .execute()
                )
                card_oracle_ids.append(oracle_id)

    print(f"Created deck for {player_name}: {deck_name}")
    return {"id": deck_id, "archetype_id": archetype_id, "cards": card_oracle_ids}


def create_tournament(tournament_data: TournamentRecord) -> Optional[str]:
    """Create tournament record if it doesn't exist."""
    tournament_name = tournament_data["t_name"]
    archived_at = tournament_data.get("archived_at")

    # Check if tournament already exists (by name AND archived_at)
    result = execute_with_retry(
        lambda: supabase.table("tournaments")
        .select("id")
        .eq("name", tournament_name)
        .eq("tournament_date", archived_at)
        .execute()
    )

    if result.data and len(result.data) > 0:
        print(
            f"Tournament '{tournament_name}' ({archived_at}) already exists. Skipping."
        )
        return None

    tournament_record = execute_with_retry(
        lambda: supabase.table("tournaments")
        .insert({"name": tournament_name, "tournament_date": archived_at})
        .execute()
    )

    return tournament_record.data[0]["id"]


def create_match(
    tournament_id: str,
    pairing: PairingRecord,
    player_decks: Dict[int, Optional[DeckInfo]],
) -> Optional[str]:
    """Create match record."""
    p1_id = pairing["p_id"]
    p2_id = pairing["opponent_id"]

    # Handle bye rounds (player ID 0)
    p1_wins = pairing["wins"] or 0
    p2_wins = pairing["losses"] or 0

    if p2_id == 0:
        # Player 1 gets a bye - automatic 2-0 win
        p1_wins = 2
        p2_wins = 0
    elif p1_id == 0:
        # Player 2 gets a bye - automatic 2-0 win
        p1_wins = 0
        p2_wins = 2

    # Determine winner
    winner_id = None
    if p1_wins > p2_wins:
        winner_id = p1_id
    elif p2_wins > p1_wins:
        winner_id = p2_id

    p1_deck = player_decks.get(p1_id)
    p2_deck = player_decks.get(p2_id)
    p1_deck_id = p1_deck.get("id") if p1_deck else None
    p2_deck_id = p2_deck.get("id") if p2_deck else None

    match_record = execute_with_retry(
        lambda: supabase.table("matches")
        .insert(
            {
                "tournament_id": tournament_id,
                "round_number": pairing["round"],
                "player1_id": p1_id,
                "player2_id": p2_id,
                "p1_wins": p1_wins,
                "p2_wins": p2_wins,
                "winner_id": winner_id if winner_id != 0 else None,
                "p1_deck_id": p1_deck_id,
                "p2_deck_id": p2_deck_id,
            }
        )
        .execute()
    )

    return match_record.data[0]["id"]


def process_archive(archive_path: str) -> None:
    """Process archive.json file and import all data."""
    with open(archive_path, "r") as f:
        data: ArchiveData = json.load(f)

    # Ensure Bye player exists
    ensure_bye_player()

    for tournament in data["tournaments"]:
        print(f"\n{'=' * 60}")
        print(
            f"Processing tournament: {tournament['t_name']} ({tournament.get('archived_at')})"
        )
        print(f"{'=' * 60}\n")

        # Create tournament
        tournament_id = create_tournament(tournament)

        # Skip if tournament already exists
        if not tournament_id:
            continue

        t_id = tournament["id"]
        archived_at = tournament.get("archived_at")

        # Get all players for this tournament (by t_id AND archived_at)
        tournament_players = [
            p
            for p in data["players"]
            if p["t_id"] == t_id and p.get("archived_at") == archived_at
        ]

        # Create players and their decks
        player_decks = {}
        for player in tournament_players:
            player_id = get_or_create_player(player["p_id"], player["name"])

            if player["deck_link"]:
                deck_info = create_deck(
                    player_id,
                    player["deck_link"],
                    player["deck_name"] or "",
                    player["name"],
                )
                player_decks[player_id] = deck_info

        # Create matches and track winrates locally
        tournament_pairings = [
            p
            for p in data["pairings"]
            if p["t_id"] == t_id and p.get("archived_at") == archived_at
        ]
        print("\n")
        processed_pairs = set()
        for pairing in tournament_pairings:
            # Avoid duplicate matches
            pair_key = tuple(sorted([pairing["p_id"], pairing["opponent_id"]]))
            if pair_key in processed_pairs:
                continue
            processed_pairs.add(pair_key)

            # Only process if match has results
            if pairing["wins"] is None:
                continue

            create_match(tournament_id, pairing, player_decks)

            print(f"Created match: Round {pairing['round']}")

        print(f"\nTournament '{tournament['t_name']}' processed successfully!")


if __name__ == "__main__":
    archive_file = "output.json"

    if not os.path.exists(archive_file):
        print(f"Error: {archive_file} not found!")
        exit(1)

    process_archive(archive_file)

    print("Recalculating Elo ratings...")
    execute_with_retry(lambda: supabase.rpc("recalibrate_elo").execute())
