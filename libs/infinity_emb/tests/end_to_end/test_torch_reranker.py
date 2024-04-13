import pytest
import torch
from asgi_lifespan import LifespanManager
from httpx import AsyncClient
from transformers import pipeline  # type: ignore[import-untyped]

from infinity_emb import create_server
from infinity_emb.args import EngineArgs
from infinity_emb.primitives import Device, InferenceEngine

PREFIX = "/v1_ct2"
MODEL: str = pytest.DEFAULT_RERANKER_MODEL  # type: ignore[assignment]
batch_size = 32 if torch.cuda.is_available() else 8

app = create_server(
    url_prefix=PREFIX,
    engine_args_list=[
        EngineArgs(
            model_name_or_path=MODEL,
            batch_size=batch_size,
            engine=InferenceEngine.torch,
            device=Device.auto if not torch.backends.mps.is_available() else Device.cpu,
        )
    ],
)


@pytest.fixture
def model_base() -> pipeline:
    return pipeline(model=MODEL, task="text-classification")


@pytest.fixture()
async def client():
    async with AsyncClient(
        app=app, base_url="http://test", timeout=20
    ) as client, LifespanManager(app):
        yield client


def test_load_model(model_base):
    # this makes sure that the error below is not based on a slow download
    # or internal pytorch errors
    assert (
        1.0
        > model_base.predict(
            {
                "text": "Where is New York?",
                "text_pair": "New York is in the united states",
            }
        )["score"]
        > 0.9
    )


@pytest.mark.anyio
async def test_model_route(client):
    response = await client.get(f"{PREFIX}/models")
    assert response.status_code == 200
    rdata = response.json()
    assert "data" in rdata
    assert rdata["data"][0].get("id", "") == MODEL
    assert isinstance(rdata["data"][0].get("stats"), dict)


@pytest.mark.anyio
async def test_reranker(client, model_base, helpers):
    query = "Where is the Eiffel Tower located?"
    documents = [
        "The Eiffel Tower is located in Paris, France",
        "The Eiffel Tower is located in the United States.",
        "The Eiffel Tower is located in the United Kingdom.",
    ]
    response = await client.post(
        f"{PREFIX}/rerank",
        json={"query": query, "documents": documents},
    )
    assert response.status_code == 200
    rdata = response.json()
    assert "model" in rdata
    assert "usage" in rdata
    rdata_results = rdata["results"]

    predictions = [
        model_base.predict({"text": query, "text_pair": doc}) for doc in documents
    ]

    assert len(rdata_results) == len(predictions)
    for i, pred in enumerate(predictions):
        assert abs(rdata_results[i]["relevance_score"] - pred["score"]) < 0.01
