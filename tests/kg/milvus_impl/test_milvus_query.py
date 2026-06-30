import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from lightrag.kg.milvus_impl import MilvusVectorDBStorage

pytestmark = pytest.mark.offline


class MockEmbeddingFunc:
    embedding_dim = 8
    max_token_size = 512
    model_name = "mock-embed"

    async def __call__(self, texts, **kwargs):
        return np.ones((len(texts), self.embedding_dim), dtype=np.float32)


def _make_storage(*, metric_type="COSINE", threshold=0.2):
    with patch(
        "lightrag.kg.milvus_impl.get_namespace_lock",
        return_value=MagicMock(),
    ):
        storage = MilvusVectorDBStorage(
            namespace="chunks",
            workspace="test",
            global_config={
                "embedding_batch_num": 10,
                "vector_db_storage_cls_kwargs": {
                    "cosine_better_than_threshold": threshold,
                    "metric_type": metric_type,
                },
            },
            embedding_func=MockEmbeddingFunc(),
            meta_fields={"content", "full_doc_id"},
        )
    storage._client = MagicMock()
    storage._client.load_collection = MagicMock()
    storage._client.search = MagicMock(
        return_value=[
            [
                {
                    "id": "above",
                    "distance": 0.7,
                    "entity": {"content": "kept", "full_doc_id": "doc-1"},
                },
                {
                    "id": "below",
                    "distance": 0.1,
                    "entity": {"content": "dropped", "full_doc_id": "doc-2"},
                },
            ]
        ]
    )
    return storage


@pytest.mark.asyncio
async def test_query_does_not_use_milvus_radius_search_for_cosine_threshold():
    storage = _make_storage(metric_type="COSINE", threshold=0.2)

    results = await storage.query("hello", top_k=5, query_embedding=[1.0] * 8)

    search_params = storage._client.search.call_args.kwargs["search_params"]
    assert search_params == {"metric_type": "COSINE", "params": {}}
    assert [result["id"] for result in results] == ["above"]


@pytest.mark.asyncio
async def test_query_filters_l2_distance_at_threshold_after_search():
    storage = _make_storage(metric_type="L2", threshold=0.2)

    results = await storage.query("hello", top_k=5, query_embedding=[1.0] * 8)

    search_params = storage._client.search.call_args.kwargs["search_params"]
    assert search_params == {"metric_type": "L2", "params": {}}
    assert [result["id"] for result in results] == ["below"]
