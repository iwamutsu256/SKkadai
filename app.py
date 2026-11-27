import io
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from azure_data import AzureTableStore, MemoryTableStore, StorageConfigurationError, storage_urls

load_dotenv()


def create_app(test_config=None):
    app = Flask(__name__)
    app_service = bool(os.environ.get("WEBSITE_INSTANCE_ID"))
    blob_url, table_url = storage_urls()
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "local-development-only"),
        AZURE_STORAGE_ACCOUNT_URL=blob_url,
        AZURE_TABLES_ACCOUNT_URL=table_url,
        AZURE_STORAGE_CONNECTION_STRING=os.environ.get(
            "AZURE_STORAGE_CONNECTION_STRING", ""
        ),
        AZURE_STORAGE_CONTAINER=os.environ.get(
            "AZURE_STORAGE_CONTAINER", "problem-images"
        ),
        SEED_DEMO_DATA=os.environ.get("SEED_DEMO_DATA", "false").lower() == "true",
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=app_service,
        BLOB_CLIENT=None,
        BLOB_CONTAINER_READY=False,
        DATA_INITIALIZED=False,
    )
    if test_config:
        app.config.update(test_config)
    if app_service and not os.environ.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY must be configured in App Service settings.")
    if app.config.get("DATA_STORE") is None:
        app.config["DATA_STORE"] = (
            MemoryTableStore() if app.config.get("TESTING") else AzureTableStore(app)
        )

    @app.before_request
    def initialize_azure_data():
        if app.config["DATA_INITIALIZED"]:
            return
        store = app.config["DATA_STORE"]
        store.ensure_tables()
        if app.config["SEED_DEMO_DATA"] and not store.list("users"):
            seed_demo_data(app)
        app.config["DATA_INITIALIZED"] = True

    @app.errorhandler(StorageConfigurationError)
    def storage_not_configured(error):
        app.logger.error("Azure Table Storage configuration is missing: %s", error)
        return render_template("storage_error.html"), 503

    @app.errorhandler(AzureError)
    def azure_storage_error(error):
        app.logger.exception("Azure Storage request failed")
        return render_template("storage_error.html"), 503

    @app.errorhandler(413)
    def request_entity_too_large(error):
        flash("画像は5MB以下にしてください。")
        return redirect(request.referrer or url_for("problem_new")), 413

    @app.context_processor
    def inject_user():
        user = None
        if session.get("user_id"):
            user = get_store(app).get("users", session["user_id"])
        return {"current_user": user}

    @app.route("/")
    def index():
        store = get_store(app)
        problems = store.list("problems", group_id=None)
        for problem in problems:
            problem["display_name"] = display_name(store, problem.get("user_id"))
            problem["like_count"] = len(store.list("likes", problem_id=problem["id"]))
        problems.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        groups = store.list("groups", visibility="public")
        groups.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return render_template("index.html", problems=problems[:12], groups=groups[:6])

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            name = request.form.get("display_name", "").strip()
            password = request.form.get("password", "")
            store = get_store(app)
            if not email or not name or len(password) < 6:
                flash("メールアドレス、表示名、6文字以上のパスワードを入力してください。")
                return render_template("auth.html", mode="register")
            if any(user["email"] == email for user in store.list("users")):
                flash("このメールアドレスは既に登録されています。")
                return render_template("auth.html", mode="register")
            user = store.add("users", {
                "email": email,
                "password_hash": generate_password_hash(password),
                "display_name": name,
                "bio": "",
                "strong_subject": "",
                "is_admin": False,
                "created_at": timestamp(),
            })
            session["user_id"] = user["id"]
            flash("登録しました。")
            return redirect(url_for("index"))
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = next(
                (item for item in get_store(app).list("users") if item["email"] == email),
                None,
            )
            if user and check_password_hash(user["password_hash"], password):
                session["user_id"] = user["id"]
                flash("ログインしました。")
                return redirect(url_for("index"))
            flash("メールアドレスまたはパスワードが違います。")
        return render_template("auth.html", mode="login")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("ログアウトしました。")
        return redirect(url_for("index"))

    @app.route("/problems")
    def problems():
        store = get_store(app)
        keyword = request.args.get("q", "").strip().casefold()
        subject = request.args.get("subject", "").strip()
        sort = request.args.get("sort", "new")
        rows = store.list("problems", group_id=None)
        for problem in rows:
            problem["display_name"] = display_name(store, problem.get("user_id"))
            problem["like_count"] = len(store.list("likes", problem_id=problem["id"]))
        if keyword:
            rows = [
                row for row in rows
                if keyword in row.get("title", "").casefold()
                or keyword in row.get("body", "").casefold()
            ]
        if subject:
            rows = [row for row in rows if row.get("subject") == subject]
        rows.sort(
            key=lambda item: (
                item.get("like_count", 0) if sort == "popular"
                else item.get("created_at", "")
            ),
            reverse=True,
        )
        subjects = sorted({row.get("subject", "") for row in store.list("problems")})
        return render_template(
            "problems.html",
            problems=rows,
            subjects=[{"subject": value} for value in subjects],
            keyword=request.args.get("q", ""),
            subject=subject,
        )

    @app.route("/problems/new", methods=["GET", "POST"])
    @app.route("/groups/<group_id>/problems/new", methods=["GET", "POST"])
    def problem_new(group_id=None):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if group_id:
            require_group_member(app, group_id, session["user_id"])
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            subject = request.form.get("subject", "").strip()
            body = request.form.get("body", "").strip()
            if not title or not subject or not body:
                flash("タイトル、教科、問題文は必須です。")
                return render_template("problem_form.html", problem=None, group_id=group_id), 400
            image_key = save_uploaded_image(app, request.files.get("image"))
            if image_key is False:
                return render_template("problem_form.html", problem=None, group_id=group_id), 400
            problem = get_store(app).add("problems", {
                "user_id": session["user_id"],
                "group_id": group_id,
                "title": title,
                "subject": subject,
                "field": request.form.get("field", "").strip(),
                "difficulty": request.form.get("difficulty", "普通"),
                "body": body,
                "answer": request.form.get("answer", "").strip(),
                "explanation": request.form.get("explanation", "").strip(),
                "image_url": image_key or "",
                "created_at": timestamp(),
            })
            record_activity(app, session["user_id"], "post")
            flash("問題を投稿しました。")
            return redirect(url_for("problem_detail", problem_id=problem["id"]))
        return render_template("problem_form.html", problem=None, group_id=group_id)

    @app.route("/problems/<problem_id>")
    def problem_detail(problem_id):
        store = get_store(app)
        problem = store.get("problems", problem_id)
        if not problem:
            return render_template("404.html"), 404
        problem["display_name"] = display_name(store, problem.get("user_id"))
        problem["like_count"] = len(store.list("likes", problem_id=problem_id))
        answers = store.list("answers", problem_id=problem_id)
        for answer in answers:
            answer["display_name"] = display_name(store, answer.get("user_id"))
            answer["like_count"] = len(store.list("likes", answer_id=answer["id"]))
        answers.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        comments = {}
        for answer in answers:
            answer_comments = store.list("comments", answer_id=answer["id"])
            for comment in answer_comments:
                comment["display_name"] = display_name(store, comment.get("user_id"))
            comments[answer["id"]] = sorted(
                answer_comments, key=lambda item: item.get("created_at", "")
            )
        user_id = session.get("user_id")
        bookmarked = bool(user_id and store.list(
            "bookmarks", problem_id=problem_id, user_id=user_id
        ))
        liked = bool(user_id and store.list("likes", problem_id=problem_id, user_id=user_id))
        return render_template(
            "problem_detail.html",
            problem=problem,
            answers=answers,
            comments=comments,
            bookmarked=bookmarked,
            liked=liked,
        )

    @app.route("/problems/<problem_id>/edit", methods=["GET", "POST"])
    def problem_edit(problem_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        store = get_store(app)
        problem = store.get("problems", problem_id)
        if not problem or problem["user_id"] != session["user_id"]:
            return render_template("404.html"), 403
        if request.method == "POST":
            image_key = save_uploaded_image(app, request.files.get("image"))
            if image_key is False:
                return render_template("problem_form.html", problem=problem), 400
            problem.update({
                "title": request.form.get("title", "").strip(),
                "subject": request.form.get("subject", "").strip(),
                "field": request.form.get("field", "").strip(),
                "difficulty": request.form.get("difficulty", "普通"),
                "body": request.form.get("body", "").strip(),
                "answer": request.form.get("answer", "").strip(),
                "explanation": request.form.get("explanation", "").strip(),
                "created_at": problem.get("created_at", timestamp()),
            })
            if image_key:
                problem["image_url"] = image_key
            store.save("problems", problem_id, problem)
            flash("問題を更新しました。")
            return redirect(url_for("problem_detail", problem_id=problem_id))
        return render_template("problem_form.html", problem=problem)

    @app.route("/problems/<problem_id>/delete", methods=["POST"])
    def problem_delete(problem_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        store = get_store(app)
        problem = store.get("problems", problem_id)
        if not problem or problem["user_id"] != session["user_id"]:
            return render_template("404.html"), 403
        for answer in store.list("answers", problem_id=problem_id):
            for comment in store.list("comments", answer_id=answer["id"]):
                store.delete("comments", comment["id"])
            store.delete("answers", answer["id"])
        for collection in ("likes", "bookmarks"):
            for entity in store.list(collection, problem_id=problem_id):
                store.delete(collection, entity["id"])
        store.delete("problems", problem_id)
        flash("問題を削除しました。")
        return redirect(url_for("problems"))

    @app.route("/problems/<problem_id>/answer", methods=["POST"])
    def answer_post(problem_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if not get_store(app).get("problems", problem_id):
            return render_template("404.html"), 404
        body = request.form.get("body", "").strip()
        if body:
            get_store(app).add("answers", {
                "problem_id": problem_id,
                "user_id": session["user_id"],
                "body": body,
                "is_correct": False,
                "created_at": timestamp(),
            })
            record_activity(app, session["user_id"], "answer")
            flash("解答を投稿しました。")
        return redirect(url_for("problem_detail", problem_id=problem_id))

    @app.route("/answers/<answer_id>/comment", methods=["POST"])
    def comment_post(answer_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        store = get_store(app)
        answer = store.get("answers", answer_id)
        if not answer:
            return render_template("404.html"), 404
        body = request.form.get("body", "").strip()
        if body:
            store.add("comments", {
                "answer_id": answer_id,
                "user_id": session["user_id"],
                "body": body,
                "created_at": timestamp(),
            })
            record_activity(app, session["user_id"], "comment")
        return redirect(url_for("problem_detail", problem_id=answer["problem_id"]))

    @app.route("/problems/<problem_id>/like", methods=["POST"])
    def problem_like(problem_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        store = get_store(app)
        key = f"p-{problem_id}-{session['user_id']}"
        if store.get("likes", key):
            store.delete("likes", key)
        else:
            store.add("likes", {
                "problem_id": problem_id,
                "user_id": session["user_id"],
                "created_at": timestamp(),
            }, entity_id=key)
        return redirect(url_for("problem_detail", problem_id=problem_id))

    @app.route("/problems/<problem_id>/bookmark", methods=["POST"])
    def problem_bookmark(problem_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        store = get_store(app)
        key = f"p-{problem_id}-{session['user_id']}"
        if store.get("bookmarks", key):
            store.delete("bookmarks", key)
        else:
            store.add("bookmarks", {
                "problem_id": problem_id,
                "user_id": session["user_id"],
                "created_at": timestamp(),
            }, entity_id=key)
        return redirect(url_for("problem_detail", problem_id=problem_id))

    @app.route("/profile")
    def profile():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        store = get_store(app)
        user_id = session["user_id"]
        user = store.get("users", user_id)
        user_problems = store.list("problems", user_id=user_id)
        user_answers = store.list("answers", user_id=user_id)
        bookmarks = []
        for bookmark in store.list("bookmarks", user_id=user_id):
            problem = store.get("problems", bookmark["problem_id"])
            if problem:
                bookmarks.append(problem)
        bookmarks.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        activities = store.list("activities", user_id=user_id)
        cutoff = date.today() - timedelta(days=29)
        activity_map = {}
        for activity in activities:
            if activity["activity_date"] >= cutoff.isoformat():
                activity_map[activity["activity_date"]] = (
                    activity_map.get(activity["activity_date"], 0) + 1
                )
        streak, longest = calculate_streak(activities)
        goal = next(
            (
                item for item in store.list("goals", user_id=user_id)
                if item.get("period") == "daily"
            ),
            None,
        )
        goal_done = 0
        if goal:
            goal_done = sum(
                1 for activity in activities
                if activity["activity_date"] == date.today().isoformat()
                and activity["activity_type"] == goal["goal_type"]
            )
        return render_template(
            "profile.html",
            user=user,
            posted=len(user_problems),
            answered=len(user_answers),
            bookmarks=bookmarks,
            activity_map=activity_map,
            streak=streak,
            longest=longest,
            goal=goal,
            goal_done=goal_done,
        )

    @app.route("/profile/goal", methods=["POST"])
    def profile_goal():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        target_count = request.form.get("target_count", type=int)
        goal_type = request.form.get("goal_type")
        if target_count is None or not 1 <= target_count <= 100 or goal_type not in {
            "answer", "post", "comment"
        }:
            flash("目標の内容が正しくありません。")
            return redirect(url_for("profile"))
        store = get_store(app)
        existing = next(
            (
                item for item in store.list("goals", user_id=session["user_id"])
                if item.get("period") == "daily"
            ),
            None,
        )
        values = {
            "user_id": session["user_id"],
            "goal_type": goal_type,
            "target_count": target_count,
            "period": "daily",
            "created_at": existing.get("created_at", timestamp()) if existing else timestamp(),
        }
        if existing:
            store.save("goals", existing["id"], values)
        else:
            store.add("goals", values)
        flash("学習目標を更新しました。")
        return redirect(url_for("profile"))

    @app.route("/groups")
    def groups():
        store = get_store(app)
        rows = store.list("groups", visibility="public")
        for group in rows:
            group["display_name"] = display_name(store, group.get("creator_id"))
            group["member_count"] = len(store.list("members", group_id=group["id"]))
        rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return render_template("groups.html", groups=rows)

    @app.route("/groups/new", methods=["GET", "POST"])
    def group_new():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if request.method == "POST":
            store = get_store(app)
            group = store.add("groups", {
                "creator_id": session["user_id"],
                "name": request.form.get("name", "").strip(),
                "purpose": request.form.get("purpose", "").strip(),
                "subject": request.form.get("subject", "").strip(),
                "target_date": request.form.get("target_date", "").strip(),
                "description": request.form.get("description", "").strip(),
                "visibility": request.form.get("visibility", "public"),
                "created_at": timestamp(),
            })
            store.add(
                "members",
                {"group_id": group["id"], "user_id": session["user_id"], "joined_at": timestamp()},
                entity_id=f"{group['id']}-{session['user_id']}",
            )
            flash("グループを作成しました。")
            return redirect(url_for("group_detail", group_id=group["id"]))
        return render_template("group_form.html")

    @app.route("/groups/<group_id>")
    def group_detail(group_id):
        store = get_store(app)
        group = store.get("groups", group_id)
        if not group:
            return render_template("404.html"), 404
        member = bool(
            session.get("user_id")
            and store.get("members", f"{group_id}-{session['user_id']}")
        )
        if group.get("visibility") == "private" and not member:
            return render_template("404.html"), 404
        group["member_count"] = len(store.list("members", group_id=group_id))
        problem_rows = store.list("problems", group_id=group_id)
        for problem in problem_rows:
            problem["display_name"] = display_name(store, problem.get("user_id"))
        problem_rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return render_template(
            "group_detail.html",
            group=group,
            problems=problem_rows,
            member=member,
        )

    @app.route("/groups/<group_id>/join", methods=["POST"])
    def group_join(group_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        store = get_store(app)
        group = store.get("groups", group_id)
        if not group or group.get("visibility") != "public":
            return render_template("404.html"), 404
        key = f"{group_id}-{session['user_id']}"
        if not store.get("members", key):
            store.add(
                "members",
                {"group_id": group_id, "user_id": session["user_id"], "joined_at": timestamp()},
                entity_id=key,
            )
        flash("グループに参加しました。")
        return redirect(url_for("group_detail", group_id=group_id))

    @app.route("/media/<path:key>")
    def media(key):
        if app.config.get("TESTING") and key.startswith("test/"):
            from flask import send_from_directory

            return send_from_directory(
                app.config["UPLOAD_FOLDER"],
                key.removeprefix("test/"),
            )
        if not key.startswith("blob/"):
            abort(404)
        blob_name = key.removeprefix("blob/")
        if "/" in blob_name or "\\" in blob_name:
            abort(404)
        try:
            blob = get_blob_service(app).get_blob_client(
                container=app.config["AZURE_STORAGE_CONTAINER"],
                blob=blob_name,
            )
            properties = blob.get_blob_properties()
            content = blob.download_blob().readall()
        except ResourceNotFoundError:
            abort(404)
        except (AzureError, StorageConfigurationError):
            app.logger.exception("Unable to read problem image from Blob Storage")
            abort(503)
        return send_file(
            io.BytesIO(content),
            mimetype=properties.content_settings.content_type or "application/octet-stream",
            download_name=blob_name,
        )

    return app


def get_store(app):
    return app.config["DATA_STORE"]


def display_name(store, user_id):
    user = store.get("users", user_id) if user_id else None
    return user["display_name"] if user else "利用者"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def record_activity(app, user_id, activity_type):
    get_store(app).add("activities", {
        "user_id": user_id,
        "activity_type": activity_type,
        "activity_date": date.today().isoformat(),
        "created_at": timestamp(),
    })


def calculate_streak(activities):
    days = {item["activity_date"] for item in activities}
    if not days:
        return 0, 0
    sorted_days = sorted(date.fromisoformat(item) for item in days)
    longest = 0
    current = 0
    previous = None
    for day in sorted_days:
        current = current + 1 if previous and day == previous + timedelta(days=1) else 1
        longest = max(longest, current)
        previous = day
    cursor = date.today()
    if cursor.isoformat() not in days:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak, longest


def require_group_member(app, group_id, user_id):
    store = get_store(app)
    group = store.get("groups", group_id)
    if not group:
        abort(404)
    if group.get("visibility") == "private" and not store.get(
        "members", f"{group_id}-{user_id}"
    ):
        abort(403)


def get_blob_service(app):
    if app.config["BLOB_CLIENT"] is None:
        connection_string = app.config["AZURE_STORAGE_CONNECTION_STRING"]
        account_url = app.config["AZURE_STORAGE_ACCOUNT_URL"]
        if connection_string:
            app.config["BLOB_CLIENT"] = BlobServiceClient.from_connection_string(
                connection_string
            )
        elif account_url:
            app.config["BLOB_CLIENT"] = BlobServiceClient(
                account_url=account_url,
                credential=DefaultAzureCredential(),
            )
        else:
            raise StorageConfigurationError(
                "Configure AZURE_STORAGE_ACCOUNT_URL or AZURE_STORAGE_CONNECTION_STRING."
            )
    service = app.config["BLOB_CLIENT"]
    if not app.config["BLOB_CONTAINER_READY"]:
        try:
            service.create_container(app.config["AZURE_STORAGE_CONTAINER"])
        except ResourceExistsError:
            pass
        app.config["BLOB_CONTAINER_READY"] = True
    return service


def save_uploaded_image(app, upload):
    if upload is None or not upload.filename:
        return ""
    allowed_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    extension = Path(upload.filename).suffix.lower()
    content_type = allowed_types.get(extension)
    if not content_type:
        flash("画像はJPG、PNG、WebP形式にしてください。")
        return False
    content = upload.stream.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        flash("画像は5MB以下にしてください。")
        return False
    filename = f"{uuid.uuid4().hex}{extension}"
    if app.config.get("TESTING"):
        Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
        Path(app.config["UPLOAD_FOLDER"], filename).write_bytes(content)
        return f"test/{filename}"
    service = get_blob_service(app)
    service.get_blob_client(
        container=app.config["AZURE_STORAGE_CONTAINER"],
        blob=filename,
    ).upload_blob(
        content,
        overwrite=False,
        content_settings=ContentSettings(content_type=content_type),
    )
    return f"blob/{filename}"


def seed_demo_data(app):
    store = get_store(app)
    user = store.add("users", {
        "email": "demo@example.com",
        "password_hash": generate_password_hash("demo1234"),
        "display_name": "デモユーザー",
        "bio": "高校学習を楽しく続けたいです。",
        "strong_subject": "数学",
        "is_admin": False,
        "created_at": timestamp(),
    })
    group = store.add("groups", {
        "creator_id": user["id"],
        "name": "〇〇大学 合格目標",
        "purpose": "共通テスト対策",
        "subject": "数学・英語",
        "target_date": (date.today() + timedelta(days=180)).isoformat(),
        "description": "毎日少しずつ問題を出し合う学習グループです。",
        "visibility": "public",
        "created_at": timestamp(),
    })
    store.add(
        "members",
        {"group_id": group["id"], "user_id": user["id"], "joined_at": timestamp()},
        entity_id=f"{group['id']}-{user['id']}",
    )
    seed_problems = [
        ("一次関数のグラフ", "数学", "一次関数", "基礎",
         "y=2x+3について、x=4のときのyの値を求めなさい。",
         "11", "xに4を代入するとy=2×4+3=11です。", None),
        ("英語の不規則変化", "英語", "不規則動詞", "基礎",
         "goの過去形を答えなさい。",
         "went", "goは不規則動詞で、過去形はwentです。", None),
        ("光合成の場所", "理科", "生物", "基礎",
         "植物で光合成が行われる主な細胞小器官を答えなさい。",
         "葉緑体", "葉緑体には光合成に必要なクロロフィルが含まれます。", None),
    ]
    for title, subject, field, difficulty, body, answer, explanation, group_id in seed_problems:
        store.add("problems", {
            "user_id": user["id"],
            "group_id": group_id,
            "title": title,
            "subject": subject,
            "field": field,
            "difficulty": difficulty,
            "body": body,
            "answer": answer,
            "explanation": explanation,
            "image_url": "",
            "created_at": timestamp(),
        })
    for offset in range(6):
        store.add("activities", {
            "user_id": user["id"],
            "activity_type": "answer",
            "activity_date": (date.today() - timedelta(days=offset)).isoformat(),
            "created_at": timestamp(),
        })
    store.add("goals", {
        "user_id": user["id"],
        "goal_type": "answer",
        "target_count": 3,
        "period": "daily",
        "created_at": timestamp(),
    })


app = create_app()

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5051")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
