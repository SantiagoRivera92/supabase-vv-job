#!/usr/bin/env python3

import json
import requests
import sys
import os
import tempfile
import subprocess
from typing import Dict, List, Set, Optional, Any
from datetime import datetime
import ijson


class CardPrioritizer:
    def __init__(self):
        self.weighs_data = self.load_weighs()
        self.prioritized_cards = set(self.weighs_data.get("prioritize", []))
        self.deprioritized_cards = set(self.weighs_data.get("deprioritize", []))
        self.ignored_cards = set(self.weighs_data.get("ignored", []))
        self.processed_cards = (
            self.prioritized_cards | self.deprioritized_cards | self.ignored_cards
        )
        self.cache_file = "scryfall_cache.json"
        self.cache_info_file = "scryfall_cache_info.json"

    def load_weighs(self) -> Dict[str, List[str]]:
        try:
            with open("weighs.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            print("weighs.json not found. Creating new file.")
            return {"prioritize": [], "deprioritize": [], "ignored": []}
        except json.JSONDecodeError as e:
            print(f"Error parsing weighs.json: {e}")
            sys.exit(1)

    def save_weighs(self):
        try:
            with open("weighs.json", "w", encoding="utf-8") as f:
                json.dump(self.weighs_data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving weighs.json: {e}")
            sys.exit(1)

    def check_cache_validity(self, current_updated_at: str) -> bool:
        try:
            with open(self.cache_info_file, "r") as f:
                cache_info = json.load(f)
            return cache_info.get("updated_at") == current_updated_at
        except FileNotFoundError:
            return False

    def save_cache_info(self, updated_at: str):
        cache_info = {"updated_at": updated_at, "cached_at": datetime.now().isoformat()}
        with open(self.cache_info_file, "w") as f:
            json.dump(cache_info, f, indent=2)

    def save_cards_cache(self, cards: List[Dict[str, Any]], updated_at: str):
        print(f"Saving {len(cards)} cards to cache...")
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(cards, f, indent=2)
            self.save_cache_info(updated_at)
            print("Cache saved successfully.")
        except Exception as e:
            print(f"Error saving cache: {e}")

    def display_card_image(self, card_name: str):
        try:
            print("Fetching card image...")
            # URL encode the card name for the API
            from urllib.parse import quote

            encoded_name = quote(card_name)
            image_url = f"https://api.scryfall.com/cards/named?exact={encoded_name}&format=image"

            response = requests.get(image_url, stream=True)
            response.raise_for_status()

            # Save to temporary file
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        temp_file.write(chunk)
                temp_file_path = temp_file.name

            try:
                # Try different methods based on platform
                if sys.platform == "darwin":  # macOS
                    subprocess.run(["open", temp_file_path], check=False)
                elif sys.platform == "win32":  # Windows
                    subprocess.run(["start", temp_file_path], shell=True, check=False)
                else:  # Linux and others
                    # Try common image viewers
                    viewers = ["xdg-open", "eog", "display", "feh"]
                    for viewer in viewers:
                        try:
                            subprocess.run([viewer, temp_file_path], check=True)
                            break
                        except (subprocess.CalledProcessError, FileNotFoundError):
                            continue
                    else:
                        print(f"Image saved to: {temp_file_path}")
                        print(
                            "No suitable image viewer found. Please open the file manually."
                        )
                        return

                # Clean up after a delay on macOS/Linux to ensure the viewer can open it
                if sys.platform != "win32":
                    import threading

                    def cleanup():
                        import time

                        time.sleep(2)
                        try:
                            os.remove(temp_file_path)
                        except:
                            pass

                    threading.Thread(target=cleanup, daemon=True).start()
                else:
                    # On Windows, remove immediately after opening
                    try:
                        os.remove(temp_file_path)
                    except:
                        pass

            except Exception as e:
                print(f"Error displaying image: {e}")
                print(f"Image saved to: {temp_file_path}")

        except requests.RequestException as e:
            print(f"Error fetching card image: {e}")
        except Exception as e:
            print(f"Error displaying card image: {e}")

    def download_and_process_scryfall(self) -> List[Dict[str, Any]]:
        print("Fetching Scryfall metadata...")
        try:
            response = requests.get("https://api.scryfall.com/bulk-data")
            response.raise_for_status()
            bulk_data = response.json()

            target = None
            for item in bulk_data["data"]:
                if item["type"] == "default_cards":
                    target = item
                    break

            if not target:
                raise Exception("Could not find default_cards in Scryfall API.")

            current_updated_at = target["updated_at"]
            print(f"Target file: {current_updated_at}")

            # Check if we have a valid cache
            if self.check_cache_validity(current_updated_at) and os.path.exists(
                self.cache_file
            ):
                print("Using cached data...")
                cached_data = self.load_cached_data()
                if cached_data is not None:
                    return cached_data
                else:
                    print("Cache invalid, re-downloading...")

            print("Downloading 500MB+ Scryfall data...")

            # Download to file first to handle large data
            temp_file = "temp_scryfall_data.json"
            try:
                response = requests.get(target["download_uri"], stream=True)
                response.raise_for_status()

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0

                with open(temp_file, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                percent = (downloaded / total_size) * 100
                                print(
                                    f"\rDownloading: {percent:.1f}%", end="", flush=True
                                )

                print(f"\nProcessing downloaded file...")

                # Process in streaming fashion to avoid memory issues
                card_dict = {}

                with open(temp_file, "r", encoding="utf-8") as f:
                    try:
                        # Use ijson for streaming JSON parsing if available

                        cards = ijson.items(f, "item")
                        for card in cards:
                            self.process_single_card(card, card_dict)
                    except ImportError:
                        # Fall back to regular JSON if ijson not available
                        print("ijson not available. Processing may use more memory...")
                        cards_data = json.load(f)
                        for card in cards_data:
                            self.process_single_card(card, card_dict)

                # Clean up temp file
                os.remove(temp_file)

                cards = list(card_dict.values())

                # Save to cache
                self.save_cards_cache(cards, current_updated_at)

                return cards

            except Exception as e:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                raise e

        except (MemoryError, requests.RequestException) as e:
            if isinstance(e, MemoryError):
                print("Memory error: The file is too large to process.")
                print("Try installing ijson for streaming: pip install ijson")
            else:
                print(f"Error downloading data: {e}")
            sys.exit(1)

    def load_cached_data(self) -> Optional[List[Dict[str, Any]]]:
        print("Loading cached card data...")
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading cached data: {e}")
            # Fallback to re-downloading
            return None

    def save_cached_data(self, cards: List[Dict[str, Any]], updated_at: str):
        print(f"Saving {len(cards)} cards to cache...")
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(cards, f, indent=2)
            self.save_cache_info(updated_at)
            print("Cache saved successfully.")
        except Exception as e:
            print(f"Error saving cache: {e}")

    def process_single_card(self, card: Dict[str, Any], card_dict: Dict[str, Any]):
        # Basic Filters
        if card.get("legalities", {}).get("vintage") == "not_legal" or not card.get(
            "oracle_id"
        ):
            return

        if (
            card.get("set_name") == "Summer Magic / Edgar"
            or card.get("set_type") == "memorabilia"
        ):
            return

        # Price Calculation
        prices = []
        for price_key in ["usd", "usd_foil", "usd_etched"]:
            price = card.get("prices", {}).get(price_key)
            if price:
                try:
                    prices.append(float(price))
                except ValueError:
                    pass

        if not prices:
            return

        min_price = min(prices)

        # Skip cards that are $5 or more
        if min_price >= 5.0:
            return

        # Cheapest Printing Logic
        oracle_id = card["oracle_id"]
        if oracle_id not in card_dict or card_dict[oracle_id]["price"] > min_price:
            card_dict[oracle_id] = {
                "oracle_id": oracle_id,
                "name": card["name"],
                "edhrec_rank": card.get("edhrec_rank"),
                "tcgplayer_id": card.get("tcgplayer_id"),
                "price": min_price,
                "set_name": card.get("set_name", ""),
                "set_type": card.get("set_type", ""),
            }

    def run_interactive_session(self, processed_cards: List[Dict[str, Any]]):
        unprocessed_cards = [
            card for card in processed_cards if card["name"] not in self.processed_cards
        ]

        if not unprocessed_cards:
            print("No new cards to process!")
            return

        # Sort by EDHREC rank (lower rank = more popular), put cards without rank at the end
        unprocessed_cards.sort(
            key=lambda card: (
                card.get("edhrec_rank") is None,
                card.get("edhrec_rank", float("inf")),
            )
        )

        print(f"\nFound {len(unprocessed_cards)} new cards to review.")
        print(
            "Options: i=image, 1=prioritize, 2=deprioritize, 3=none, 4=ignore, q=quit"
        )
        print("-" * 50)

        for i, card in enumerate(unprocessed_cards):
            print(f"\nCard {i + 1}/{len(unprocessed_cards)}")
            print(f"Name: {card['name']}")
            print(f"Set: {card['set_name']}")
            print(f"EDHREC Rank: {card.get('edhrec_rank', 'N/A')}")
            print(f"Price: ${card['price']:.2f}")

            while True:
                try:
                    choice = (
                        input(
                            "Choice (i=image, 1=prioritize, 2=deprioritize, 3=none, 4=ignore, q=quit): "
                        )
                        .strip()
                        .lower()
                    )

                    if choice == "i":
                        self.display_card_image(card["name"])
                        continue

                    elif choice == "q":
                        print(f"\nSaving progress and quitting...")
                        self.save_weighs()
                        print(f"Processed {i} cards in this session.")
                        return

                    elif choice == "1":
                        self.prioritized_cards.add(card["name"])
                        self.weighs_data["prioritize"] = sorted(
                            list(self.prioritized_cards)
                        )
                        self.save_weighs()
                        print(f"✓ Added '{card['name']}' to prioritize list")
                        break

                    elif choice == "2":
                        self.deprioritized_cards.add(card["name"])
                        self.weighs_data["deprioritize"] = sorted(
                            list(self.deprioritized_cards)
                        )
                        self.save_weighs()
                        print(f"✓ Added '{card['name']}' to deprioritize list")
                        break

                    elif choice == "3":
                        print(f"✓ Skipped '{card['name']}'")
                        break

                    elif choice == "4":
                        self.ignored_cards.add(card["name"])
                        self.weighs_data["ignored"] = sorted(list(self.ignored_cards))
                        self.save_weighs()
                        print(
                            f"✓ Added '{card['name']}' to ignore list (won't be asked again)"
                        )
                        break

                    else:
                        print("Invalid choice. Please enter i, 1, 2, 3, 4, or q.")

                except KeyboardInterrupt:
                    print(f"\n\nInterrupted. Saving progress...")
                    self.save_weighs()
                    print(f"Processed {i} cards in this session.")
                    return

        print(f"\nCompleted all {len(unprocessed_cards)} cards!")

    def run(self):
        print("=== Card Prioritizer ===")
        print(
            f"Starting with {len(self.prioritized_cards)} prioritized and {len(self.deprioritized_cards)} deprioritized cards"
        )

        # Download and process cards in one step to save memory
        processed_cards = self.download_and_process_scryfall()

        # Run interactive session
        self.run_interactive_session(processed_cards)

        print("Session completed successfully!")


if __name__ == "__main__":
    prioritizer = CardPrioritizer()
    prioritizer.run()
