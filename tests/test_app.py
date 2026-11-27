import tempfile
from pathlib import Path

import pytest

from app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.sqlite3"),
        "SECRET_KEY": "test-secret",
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

    problem_id = 4
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

    response = client.post("/groups/1/problems/new", data={
        "title": "グループ問題",
        "subject": "数学",
        "field": "確率",
        "difficulty": "普通",
        "body": "サイコロを投げて3が出る確率は？",
        "answer": "1/6",
        "explanation": "全6通りのうち一通りです。",
    }, follow_redirects=True)
    assert "問題を投稿しました" in response.get_data(as_text=True)
