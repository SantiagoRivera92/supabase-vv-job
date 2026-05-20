import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from dotenv import load_dotenv
from supabase import create_client, Client
import numpy as np
from typing import List, Dict, Optional
from sklearn.manifold import MDS
from sklearn.cluster import KMeans

# Load environment variables
load_dotenv()

# Initialize Supabase client
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def get_archetype_heatmap_data() -> pd.DataFrame:
    """
    Fetch archetype similarity heatmap data from the database.
    Returns a DataFrame with columns: archetype_x, archetype_y, similarity_score
    """
    try:
        response = supabase.rpc("get_archetype_heatmap_matrix").execute()

        if not response.data:
            print("No data returned from get_archetype_heatmap_matrix function")
            return pd.DataFrame()

        # Convert to DataFrame
        df = pd.DataFrame(response.data)
        return df

    except Exception as e:
        print(f"Error fetching heatmap data: {e}")
        return pd.DataFrame()


def create_heatmap(
    df: pd.DataFrame,
    figsize: tuple = (16, 12),
    output_path: str = "archetype_heatmap.png",
):
    """
    Create and save a heatmap visualization from archetype similarity data.

    Args:
        df: DataFrame with archetype_x, archetype_y, similarity_score columns
        figsize: Figure size as (width, height)
        output_path: Path to save the heatmap image
    """
    if df.empty:
        print("No data available for heatmap visualization")
        return

    # Pivot the data to create a matrix
    heatmap_matrix = df.pivot_table(
        index="archetype_x", columns="archetype_y", values="similarity_score"
    )

    # Ensure the matrix is symmetric (fill missing values)
    heatmap_matrix = heatmap_matrix.fillna(heatmap_matrix.T).fillna(0)

    # Create figure and axis
    plt.figure(figsize=figsize)

    # Create heatmap with custom styling
    sns.heatmap(
        heatmap_matrix,
        annot=True,
        fmt=".3f",
        cmap="viridis",
        center=0,
        square=True,
        cbar_kws={"label": "Similarity Score"},
        annot_kws={"size": 6},  # Smaller font for annotations
    )

    plt.title("Archetype Similarity Heatmap", fontsize=16, pad=20)
    plt.xlabel("Archetype Y", fontsize=12)
    plt.ylabel("Archetype X", fontsize=12)

    # Rotate labels for better readability
    plt.xticks(rotation=45, ha="right")
    plt.yticks(rotation=0)

    # Adjust layout to prevent label cutoff
    plt.tight_layout()

    # Save the heatmap
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    print(f"Heatmap saved to: {output_path}")

    # Show the plot
    plt.show()


def get_decks_by_archetype() -> Dict[str, List[Dict]]:
    """
    Fetch decks grouped by archetype from the database.
    Returns dict: {archetype_name: [deck_info, ...]}
    """
    try:
        # Get decks with archetype information
        response = supabase.rpc("get_decks_with_archetypes").execute()

        if not response.data:
            print("No deck data returned")
            return {}

        # Group decks by archetype
        archetype_decks = {}
        for deck in response.data:
            archetype_name = deck.get("archetype_name", "Unknown")
            if archetype_name not in archetype_decks:
                archetype_decks[archetype_name] = []
            archetype_decks[archetype_name].append(
                {
                    "id": deck.get("id"),
                    "name": deck.get("name", f"Deck {deck.get('id')}"),
                    "player": deck.get("player_name", "Unknown"),
                }
            )

        return archetype_decks

    except Exception as e:
        print(f"Error fetching deck data: {e}")
        return {}


def create_spatial_heatmap(
    df: pd.DataFrame,
    figsize: tuple = (16, 12),
    output_path: str = "spatial_archetype_heatmap.png",
    min_similarity: float = 0.1,
    max_archetypes: int = 50,
    show_decks: bool = True,
    max_decks_per_archetype: int = 10,
):
    """
    Create a spatial heatmap where archetypes are positioned based on similarity.
    Similar archetypes appear closer together, dissimilar ones farther apart.
    Individual decks are positioned near their archetype.

    Args:
        df: DataFrame with archetype_x, archetype_y, similarity_score columns
        figsize: Figure size as (width, height)
        output_path: Path to save the spatial heatmap
        min_similarity: Minimum similarity threshold to include
        max_archetypes: Maximum number of archetypes to visualize
        show_decks: Whether to show individual decks near archetypes
        max_decks_per_archetype: Maximum decks to show per archetype
    """
    if df.empty:
        print("No data available for spatial heatmap")
        return

    # Filter by minimum similarity
    filtered_df = df[df["similarity_score"] >= min_similarity].copy()

    # Get unique archetypes and limit if needed
    unique_archetypes = sorted(
        list(
            set(
                filtered_df["archetype_x"].tolist()
                + filtered_df["archetype_y"].tolist()
            )
        )
    )

    if len(unique_archetypes) > max_archetypes:
        # Select top archetypes by total similarity scores
        archetype_scores = {}
        for archetype in unique_archetypes:
            total_score = filtered_df[
                (filtered_df["archetype_x"] == archetype)
                | (filtered_df["archetype_y"] == archetype)
            ]["similarity_score"].sum()
            archetype_scores[archetype] = total_score

        top_archetypes = sorted(
            archetype_scores.items(), key=lambda x: x[1], reverse=True
        )[:max_archetypes]
        selected_archetypes = [arch for arch, _ in top_archetypes]
        unique_archetypes = selected_archetypes

    # Create similarity matrix for selected archetypes
    similarity_matrix = np.zeros((len(unique_archetypes), len(unique_archetypes)))
    archetype_to_idx = {arch: i for i, arch in enumerate(unique_archetypes)}

    for _, row in filtered_df.iterrows():
        if (
            row["archetype_x"] in archetype_to_idx
            and row["archetype_y"] in archetype_to_idx
        ):
            i, j = (
                archetype_to_idx[row["archetype_x"]],
                archetype_to_idx[row["archetype_y"]],
            )
            similarity_matrix[i, j] = row["similarity_score"]
            similarity_matrix[j, i] = row["similarity_score"]  # Make symmetric

    # Ensure diagonal is 1.0 (self-similarity)
    np.fill_diagonal(similarity_matrix, 1.0)

    # Convert similarity to distance (higher similarity = lower distance)
    distance_matrix = 1 - similarity_matrix

    # Use MDS to get 2D coordinates
    mds = MDS(n_components=2, dissimilarity="precomputed", random_state=42)
    coordinates = mds.fit_transform(distance_matrix)

    # Get deck data if requested
    archetype_decks = {}
    if show_decks:
        archetype_decks = get_decks_by_archetype()

    # Create the spatial visualization
    plt.figure(figsize=figsize)

    # Draw lines between highly similar archetypes first (so they appear behind points)
    high_similarity_threshold = 0.3
    for i, arch_x in enumerate(unique_archetypes):
        for j, arch_y in enumerate(unique_archetypes):
            if i < j and similarity_matrix[i, j] > high_similarity_threshold:
                plt.plot(
                    [coordinates[i, 0], coordinates[j, 0]],
                    [coordinates[i, 1], coordinates[j, 1]],
                    "gray",
                    alpha=0.3,
                    linewidth=0.5,
                )

    # Plot archetypes as larger points
    from matplotlib import cm

    archetype_colors = getattr(cm, "Set3", cm.tab10)(
        np.linspace(0, 1, len(unique_archetypes))
    )
    for i, archetype in enumerate(unique_archetypes):
        plt.scatter(
            coordinates[i, 0],
            coordinates[i, 1],
            s=300,
            alpha=0.8,
            c=[archetype_colors[i]],
            edgecolors="black",
            linewidth=2,
            zorder=5,
        )

    # Add archetype labels
    for i, archetype in enumerate(unique_archetypes):
        plt.annotate(
            archetype,
            (coordinates[i, 0], coordinates[i, 1]),
            xytext=(8, 8),
            textcoords="offset points",
            fontsize=10,
            fontweight="bold",
            alpha=0.9,
            zorder=6,
        )

    # Add decks near their archetypes if requested
    if show_decks and archetype_decks:
        decks_added = 0
        for i, archetype in enumerate(unique_archetypes):
            if archetype in archetype_decks:
                decks = archetype_decks[archetype][
                    :max_decks_per_archetype
                ]  # Limit decks per archetype

                for deck_idx, deck in enumerate(decks):
                    # Position decks in a circle around their archetype
                    angle = (deck_idx / max_decks_per_archetype) * 2 * np.pi
                    radius = 0.15 + (
                        deck_idx * 0.02
                    )  # Slightly different radii to avoid overlap

                    deck_x = coordinates[i, 0] + radius * np.cos(angle)
                    deck_y = coordinates[i, 1] + radius * np.sin(angle)

                    # Plot deck as smaller point
                    plt.scatter(
                        deck_x,
                        deck_y,
                        s=50,
                        alpha=0.6,
                        c=[archetype_colors[i]],
                        marker="s",
                        zorder=4,
                    )

                    # Add deck name (shortened if needed)
                    deck_name = (
                        deck["name"][:15] + "..."
                        if len(deck["name"]) > 15
                        else deck["name"]
                    )
                    plt.annotate(
                        deck_name,
                        (deck_x, deck_y),
                        xytext=(3, 3),
                        textcoords="offset points",
                        fontsize=6,
                        alpha=0.7,
                        zorder=4,
                    )

                    decks_added += 1

    plt.title(
        f"Spatial Archetype & Deck Similarity Map\n({len(unique_archetypes)} archetypes, {decks_added if show_decks else 0} decks)",
        fontsize=16,
        pad=20,
    )
    plt.xlabel("MDS Dimension 1", fontsize=12)
    plt.ylabel("MDS Dimension 2", fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()

    # Save the spatial heatmap
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    print(f"Spatial heatmap saved to: {output_path}")
    plt.show()


def create_filtered_heatmap(
    df: pd.DataFrame,
    min_similarity: float = 0.1,
    max_archetypes: int = 30,
    figsize: tuple = (14, 10),
    output_path: str = "filtered_archetype_heatmap.png",
):
    """
    Create a filtered heatmap showing only significant similarities.

    Args:
        df: DataFrame with archetype similarity data
        min_similarity: Minimum similarity threshold to display
        max_archetypes: Maximum number of archetypes to include
        figsize: Figure size as (width, height)
        output_path: Path to save the filtered heatmap
    """
    if df.empty:
        print("No data available for filtered heatmap")
        return

    # Filter by minimum similarity
    filtered_df = df[df["similarity_score"] >= min_similarity].copy()

    # Get unique archetypes and limit if needed
    unique_archetypes = sorted(
        list(
            set(
                filtered_df["archetype_x"].tolist()
                + filtered_df["archetype_y"].tolist()
            )
        )
    )

    if len(unique_archetypes) > max_archetypes:
        # Select top archetypes by total similarity scores
        archetype_scores = {}
        for archetype in unique_archetypes:
            total_score = filtered_df[
                (filtered_df["archetype_x"] == archetype)
                | (filtered_df["archetype_y"] == archetype)
            ]["similarity_score"].sum()
            archetype_scores[archetype] = total_score

        top_archetypes = sorted(
            archetype_scores.items(), key=lambda x: x[1], reverse=True
        )[:max_archetypes]
        selected_archetypes = [arch for arch, _ in top_archetypes]

        mask = filtered_df["archetype_x"].isin(selected_archetypes) & filtered_df[
            "archetype_y"
        ].isin(selected_archetypes)
        filtered_df = filtered_df[mask].copy()

    # Create filtered heatmap
    create_heatmap(filtered_df, figsize=figsize, output_path=output_path)


def analyze_similarity_patterns(df: pd.DataFrame):
    """
    Analyze and print statistics about archetype similarities.

    Args:
        df: DataFrame with archetype similarity data
    """
    if df.empty:
        print("No data available for analysis")
        return

    print("\n" + "=" * 50)
    print("ARCHETYPE SIMILARITY ANALYSIS")
    print("=" * 50)

    # Overall statistics
    print(f"Total archetype pairs: {len(df)}")
    print(f"Average similarity: {df['similarity_score'].mean():.4f}")
    print(f"Max similarity: {df['similarity_score'].max():.4f}")
    print(f"Min similarity: {df['similarity_score'].min():.4f}")

    # Most similar pairs
    print("\nTop 10 Most Similar Archetype Pairs:")
    top_similar = df.nlargest(10, "similarity_score")
    for _, row in top_similar.iterrows():
        print(
            f"  {row['archetype_x']} ↔ {row['archetype_y']}: {row['similarity_score']:.4f}"
        )

    # Self-similarities (should be 1.0)
    self_similar = df[df["archetype_x"] == df["archetype_y"]]
    if not self_similar.empty:
        print(f"\nArchetype count: {len(self_similar)}")

    # High similarity pairs (excluding self-similarity)
    high_similar = df[
        (df["similarity_score"] > 0.2) & (df["archetype_x"] != df["archetype_y"])
    ]
    if not high_similar.empty:
        print(f"\nHigh similarity pairs (>0.2): {len(high_similar)}")
        for _, row in high_similar.iterrows():
            print(
                f"  {row['archetype_x']} ↔ {row['archetype_y']}: {row['similarity_score']:.4f}"
            )


def main():
    """Main function to generate and save archetype similarity heatmap."""
    print("Fetching archetype heatmap data...")
    df = get_archetype_heatmap_data()

    if df.empty:
        print("Failed to fetch data. Exiting.")
        return

    # Display analysis
    analyze_similarity_patterns(df)

    # Create main heatmap
    print("\nGenerating full heatmap...")
    create_heatmap(df, output_path="archetype_heatmap.png")

    # Create filtered heatmap for better readability
    print("\nGenerating filtered heatmap (similarities >= 0.1)...")
    create_filtered_heatmap(
        df, min_similarity=0.1, output_path="filtered_archetype_heatmap.png"
    )

    print(
        "\nSpatial heatmap generation complete! Use spatial_heatmap.py or demo_spatial_heatmap.py for spatial visualization."
    )


if __name__ == "__main__":
    main()
