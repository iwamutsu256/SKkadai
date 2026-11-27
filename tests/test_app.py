from pathlib import Path

from io import BytesIO

import pytest

from app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app({
        "TESTING": True,
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "SECRET_KEY": "test-secret",
        "SEED_DEMO_DATA": True,
    })
    with app.test_client() as client:
        yield client


def login(client):
    return client.post("/login", data={
        "email": "demo@example.com",
        "password": "demo1234",
    }, follow_redirects=True)


def test_index_and_demo_login(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "StudyBoard" in response.get_data(as_text=True)
    response = login(client)
    assert "ログインしました" in response.get_data(as_text=True)


def test_create_answer_and_profile(client):
    login(client)
    response = client.post("/problems/new", data={
        "title": "テスト問題",
        "subject": "数学",
        "field": "一次関数",
        "difficulty": "基礎",
        "body": "1+1を答えなさい。",
        "answer": "2",
        "explanation": "1と1を足します。",
    }, follow_redirects=True)
    assert "問題を投稿しました" in response.get_data(as_text=True)

    problem_id = next(
        problem["id"]
        for problem in client.application.config["DATA_STORE"].list("problems")
        if problem["title"] == "テスト問題"
    )
    response = client.post(f"/problems/{problem_id}/answer", data={"body": "答えは2です。"}, follow_redirects=True)
    assert "解答を投稿しました" in response.get_data(as_text=True)

    response = client.get("/profile")
    assert "日連続" in response.get_data(as_text=True)
    assert "今日の目標" in response.get_data(as_text=True)


def test_group_create_join_and_search(client):
    login(client)
    response = client.post("/groups/new", data={
        "name": "定期テスト対策",
        "purpose": "中間テスト対策",
        "subject": "数学",
        "target_date": "2027-01-20",
        "description": "毎日問題を共有します。",
        "visibility": "public",
    }, follow_redirects=True)
    assert "グループを作成しました" in response.get_data(as_text=True)

    response = client.get("/groups")
    assert "定期テスト対策" in response.get_data(as_text=True)

    group_id = client.application.config["DATA_STORE"].list("groups")[-1]["id"]
    response = client.post(f"/groups/{group_id}/problems/new", data={
        "title": "グループ問題",
        "subject": "数学",
        "field": "確率",
        "difficulty": "普通",
        "body": "サイコロを投げて3が出る確率は？",
        "answer": "1/6",
        "explanation": "全6通りのうち一通りです。",
    }, follow_redirects=True)
    assert "問題を投稿しました" in response.get_data(as_text=True)


def test_local_image_upload_and_retrieval(client):
    login(client)
    response = client.post("/problems/new", data={
        "title": "画像つき問題",
        "subject": "理科",
        "field": "地学",
        "difficulty": "基礎",
        "body": "画像を確認してください。",
        "answer": "",
        "explanation": "",
        "image": (BytesIO(b"fake png bytes"), "diagram.png", "image/png"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert "画像つき問題" in response.get_data(as_text=True)

    image_path = next(Path(client.application.config["UPLOAD_FOLDER"]).glob("*.png"))
    media_response = client.get(f"/media/test/{image_path.name}")
    assert media_response.status_code == 200
    assert media_response.data == b"fake png bytes"
