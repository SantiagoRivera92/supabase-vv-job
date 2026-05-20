import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from dotenv import load_dotenv
from supabase import create_client, Client
import numpy as np
from typing import List, Dict, Optional
from sklearn.manifold import MDS

# Load environment variables
load_dotenv()

# Initialize Supabase client
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
if supabase_url and supabase_key:
    supabase: Client = create_client(supabase_url, supabase_key)


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


def create_spatial_heatmap(
    df: pd.DataFrame,
    figsize: tuple = (16, 12),
    output_path: str = "spatial_archetype_heatmap.png",
    min_similarity: float = 0.5,
    max_archetypes: int = 200,
):
    """
    Create a spatial heatmap where archetypes are positioned based on similarity.
    Similar archetypes appear closer together, dissimilar ones farther apart.

    Args:
        df: DataFrame with archetype_x, archetype_y, similarity_score columns
        figsize: Figure size as (width, height)
        output_path: Path to save the spatial heatmap
        min_similarity: Minimum similarity threshold to include
        max_archetypes: Maximum number of archetypes to visualize
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

    # Plot archetypes as points
    plt.scatter(
        coordinates[:, 0],
        coordinates[:, 1],
        s=200,
        alpha=0.8,
        c="blue",
        edgecolors="black",
        linewidth=2,
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
        )

    plt.title(
        f"Spatial Archetype Similarity Map\n({len(unique_archetypes)} archetypes, similarity >= {min_similarity})",
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


def main():
    """Main function to generate spatial archetype heatmap."""
    print("Fetching archetype heatmap data...")
    df = get_archetype_heatmap_data()

    if df.empty:
        print("Failed to fetch data. Exiting.")
        return

    print(f"Data loaded: {len(df)} archetype pairs")

    # Create spatial heatmap - positions archetypes based on similarity
    print("\nGenerating spatial heatmap (archetypes positioned by similarity)...")
    create_spatial_heatmap(
        df, output_path="spatial_archetype_heatmap.png"
    )

    print("\nSpatial heatmap generation complete!")


if __name__ == "__main__":
    main()
