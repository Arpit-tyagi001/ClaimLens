from ai.claimlens_ai.retrieval import (
    Chunk,
    build_index,
    get_retriever,
)


def main():

    chunks = [
        Chunk(
            chunk_id="chunk-001",
            doc_id="policy-001",
            section_path="Waiting Period",
            page=3,
            bbox=[0.0, 0.0, 100.0, 100.0],
            text="A waiting period of 30 days applies to illness claims.",
        ),
        Chunk(
            chunk_id="chunk-002",
            doc_id="policy-001",
            section_path="Exclusions",
            page=7,
            bbox=[0.0, 0.0, 100.0, 100.0],
            text="Pre-existing diseases are excluded from coverage.",
        ),
        Chunk(
            chunk_id="chunk-003",
            doc_id="policy-001",
            section_path="Documents",
            page=10,
            bbox=[0.0, 0.0, 100.0, 100.0],
            text="The claimant must submit the required hospital documents.",
        ),
    ]

    build_index(
        case_id="case-001",
        chunks=chunks,
    )

    retriever = get_retriever("case-001")

    results = retriever.search(
        "waiting period illness",
        top_k=2,
    )

    print("RETRIEVAL TEST PASSED")

    for result in results:
        print(
        result.chunk_id,
        "|",
        result.section_path,
        )


if __name__ == "__main__":
    main()