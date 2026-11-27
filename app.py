import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, g, redirect, render_template, request, session, url_for, flash
from werkzeug.security import check_password_hash, generate_password_hash

from db import close_db, get_db, init_db


def create_app(test_config=None):
    app = Flask(__name__)
    instance_path = Path(app.instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "local-development-only"),
        DATABASE=str(instance_path / "study_board.sqlite3"),
    )
    if test_config:
        app.config.update(test_config)

    init_db(app)
    app.teardown_appcontext(close_db)

    with app.app_context():
        if not get_db(app).execute("SELECT 1 FROM users LIMIT 1").fetchone():
            seed_demo_data(app)

    @app.context_processor
    def inject_user():
        user = None
        if session.get("user_id"):
            user = get_db(app).execute(
                "SELECT * FROM users WHERE id = ?", (session["user_id"],)
            ).fetchone()
        return {"current_user": user}

    @app.route("/")
    def index():
        db = get_db(app)
        problems = db.execute(
            """
            SELECT p.*, u.display_name,
                   (SELECT COUNT(*) FROM likes l WHERE l.problem_id=p.id) AS like_count
            FROM problems p JOIN users u ON u.id=p.user_id
            WHERE p.group_id IS NULL
            ORDER BY p.created_at DESC LIMIT 12
            """
        ).fetchall()
        groups = db.execute(
            "SELECT * FROM groups ORDER BY created_at DESC LIMIT 6"
        ).fetchall()
        return render_template("index.html", problems=problems, groups=groups)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            email = request.form["email"].strip().lower()
            name = request.form["display_name"].strip()
            password = request.form["password"]
            db = get_db(app)
            if not email or not name or len(password) < 6:
                flash("メールアドレス、表示名、6文字以上のパスワードを入力してください。")
                return render_template("auth.html", mode="register")
            if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
                flash("このメールアドレスは既に登録されています。")
                return render_template("auth.html", mode="register")
            cursor = db.execute(
                "INSERT INTO users(email,password_hash,display_name) VALUES(?,?,?)",
                (email, generate_password_hash(password), name),
            )
            db.commit()
            session["user_id"] = cursor.lastrowid
            flash("登録しました。")
            return redirect(url_for("index"))
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form["email"].strip().lower()
            password = request.form["password"]
            db = get_db(app)
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
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
        db = get_db(app)
        keyword = request.args.get("q", "").strip()
        subject = request.args.get("subject", "").strip()
        sort = request.args.get("sort", "new")
        query = """
            SELECT p.*, u.display_name,
                   (SELECT COUNT(*) FROM likes l WHERE l.problem_id=p.id) AS like_count
            FROM problems p JOIN users u ON u.id=p.user_id
            WHERE p.group_id IS NULL
        """
        params = []
        if keyword:
            query += " AND (p.title LIKE ? OR p.body LIKE ?)"
            params += [f"%{keyword}%", f"%{keyword}%"]
        if subject:
            query += " AND p.subject=?"
            params.append(subject)
        query += " ORDER BY like_count DESC" if sort == "popular" else " ORDER BY p.created_at DESC"
        rows = db.execute(query, params).fetchall()
        subjects = db.execute(
            "SELECT DISTINCT subject FROM problems WHERE group_id IS NULL ORDER BY subject"
        ).fetchall()
        return render_template("problems.html", problems=rows, subjects=subjects, keyword=keyword, subject=subject)

    @app.route("/problems/new", methods=["GET", "POST"])
    @app.route("/groups/<int:group_id>/problems/new", methods=["GET", "POST"])
    def problem_new(group_id=None):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if request.method == "POST":
            title = request.form["title"].strip()
            subject = request.form["subject"].strip()
            body = request.form["body"].strip()
            if not title or not subject or not body:
                flash("タイトル、教科、問題文は必須です。")
                return render_template("problem_form.html", problem=None, group_id=group_id)
            db = get_db(app)
            cursor = db.execute(
                """INSERT INTO problems(user_id,group_id,title,subject,field,difficulty,body,answer,explanation)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (session["user_id"], group_id, title, subject, request.form["field"].strip(),
                 request.form["difficulty"], body, request.form["answer"].strip(),
                 request.form["explanation"].strip()),
            )
            db.commit()
            record_activity(app, session["user_id"], "post")
            flash("問題を投稿しました。")
            return redirect(url_for("problem_detail", problem_id=cursor.lastrowid))
        return render_template("problem_form.html", problem=None, group_id=group_id)

    @app.route("/problems/<int:problem_id>")
    def problem_detail(problem_id):
        db = get_db(app)
        problem = db.execute(
            """SELECT p.*, u.display_name,
                      (SELECT COUNT(*) FROM likes l WHERE l.problem_id=p.id) AS like_count
               FROM problems p JOIN users u ON u.id=p.user_id WHERE p.id=?""",
            (problem_id,),
        ).fetchone()
        if not problem:
            return render_template("404.html"), 404
        answers = db.execute(
            """SELECT a.*, u.display_name,
                      (SELECT COUNT(*) FROM likes l WHERE l.answer_id=a.id) AS like_count
               FROM answers a JOIN users u ON u.id=a.user_id
               WHERE problem_id=? ORDER BY a.created_at DESC""",
            (problem_id,),
        ).fetchall()
        comments = {}
        for answer in answers:
            comments[answer["id"]] = db.execute(
                """SELECT c.*, u.display_name FROM comments c JOIN users u ON u.id=c.user_id
                   WHERE c.answer_id=? ORDER BY c.created_at""",
                (answer["id"],),
            ).fetchall()
        bookmarked = False
        liked = False
        if session.get("user_id"):
            bookmarked = db.execute(
                "SELECT 1 FROM bookmarks WHERE problem_id=? AND user_id=?",
                (problem_id, session["user_id"]),
            ).fetchone()
            liked = db.execute(
                "SELECT 1 FROM likes WHERE problem_id=? AND user_id=?",
                (problem_id, session["user_id"]),
            ).fetchone()
        return render_template("problem_detail.html", problem=problem, answers=answers,
                               comments=comments, bookmarked=bool(bookmarked), liked=bool(liked))

    @app.route("/problems/<int:problem_id>/edit", methods=["GET", "POST"])
    def problem_edit(problem_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        db = get_db(app)
        problem = db.execute("SELECT * FROM problems WHERE id=?", (problem_id,)).fetchone()
        if not problem or problem["user_id"] != session["user_id"]:
            return render_template("404.html"), 403
        if request.method == "POST":
            db.execute(
                """UPDATE problems SET title=?,subject=?,field=?,difficulty=?,body=?,answer=?,explanation=?
                   WHERE id=?""",
                (request.form["title"], request.form["subject"], request.form["field"],
                 request.form["difficulty"], request.form["body"], request.form["answer"],
                 request.form["explanation"], problem_id),
            )
            db.commit()
            flash("問題を更新しました。")
            return redirect(url_for("problem_detail", problem_id=problem_id))
        return render_template("problem_form.html", problem=problem)

    @app.route("/problems/<int:problem_id>/delete", methods=["POST"])
    def problem_delete(problem_id):
        db = get_db(app)
        problem = db.execute("SELECT * FROM problems WHERE id=?", (problem_id,)).fetchone()
        if not problem or problem["user_id"] != session.get("user_id"):
            return render_template("404.html"), 403
        db.execute("DELETE FROM problems WHERE id=?", (problem_id,))
        db.commit()
        flash("問題を削除しました。")
        return redirect(url_for("problems"))

    @app.route("/problems/<int:problem_id>/answer", methods=["POST"])
    def answer_post(problem_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        body = request.form["body"].strip()
        if body:
            db = get_db(app)
            db.execute(
                "INSERT INTO answers(problem_id,user_id,body) VALUES(?,?,?)",
                (problem_id, session["user_id"], body),
            )
            db.commit()
            record_activity(app, session["user_id"], "answer")
            flash("解答を投稿しました。")
        return redirect(url_for("problem_detail", problem_id=problem_id))

    @app.route("/answers/<int:answer_id>/comment", methods=["POST"])
    def comment_post(answer_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        db = get_db(app)
        answer = db.execute("SELECT problem_id FROM answers WHERE id=?", (answer_id,)).fetchone()
        body = request.form["body"].strip()
        if answer and body:
            db.execute(
                "INSERT INTO comments(answer_id,user_id,body) VALUES(?,?,?)",
                (answer_id, session["user_id"], body),
            )
            db.commit()
            record_activity(app, session["user_id"], "comment")
        return redirect(url_for("problem_detail", problem_id=answer["problem_id"]))

    @app.route("/problems/<int:problem_id>/like", methods=["POST"])
    def problem_like(problem_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        db = get_db(app)
        existing = db.execute(
            "SELECT id FROM likes WHERE problem_id=? AND user_id=?",
            (problem_id, session["user_id"]),
        ).fetchone()
        if existing:
            db.execute("DELETE FROM likes WHERE id=?", (existing["id"],))
        else:
            db.execute(
                "INSERT INTO likes(problem_id,user_id) VALUES(?,?)",
                (problem_id, session["user_id"]),
            )
        db.commit()
        return redirect(url_for("problem_detail", problem_id=problem_id))

    @app.route("/problems/<int:problem_id>/bookmark", methods=["POST"])
    def problem_bookmark(problem_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        db = get_db(app)
        existing = db.execute(
            "SELECT id FROM bookmarks WHERE problem_id=? AND user_id=?",
            (problem_id, session["user_id"]),
        ).fetchone()
        if existing:
            db.execute("DELETE FROM bookmarks WHERE id=?", (existing["id"],))
        else:
            db.execute(
                "INSERT INTO bookmarks(problem_id,user_id) VALUES(?,?)",
                (problem_id, session["user_id"]),
            )
        db.commit()
        return redirect(url_for("problem_detail", problem_id=problem_id))

    @app.route("/profile")
    def profile():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        db = get_db(app)
        user_id = session["user_id"]
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        posted = db.execute("SELECT COUNT(*) AS c FROM problems WHERE user_id=?", (user_id,)).fetchone()["c"]
        answered = db.execute("SELECT COUNT(*) AS c FROM answers WHERE user_id=?", (user_id,)).fetchone()["c"]
        bookmarks = db.execute(
            """SELECT p.* FROM bookmarks b JOIN problems p ON p.id=b.problem_id
               WHERE b.user_id=? ORDER BY b.created_at DESC""",
            (user_id,),
        ).fetchall()
        activity = db.execute(
            """SELECT activity_date, COUNT(*) AS count FROM learning_activity
               WHERE user_id=? AND activity_date>=?
               GROUP BY activity_date ORDER BY activity_date""",
            (user_id, (date.today() - timedelta(days=29)).isoformat()),
        ).fetchall()
        activity_map = {row["activity_date"]: row["count"] for row in activity}
        streak, longest = calculate_streak(app, user_id)
        goal = db.execute(
            "SELECT * FROM learning_goals WHERE user_id=? AND period='daily'",
            (user_id,),
        ).fetchone()
        goal_done = 0
        if goal:
            mapping = {"answer": "answers", "post": "problems", "comment": "comments"}
            column = "activity_type"
            goal_done = db.execute(
                """SELECT COUNT(*) AS c FROM learning_activity
                   WHERE user_id=? AND activity_date=? AND activity_type=?""",
                (user_id, date.today().isoformat(), goal["goal_type"]),
            ).fetchone()["c"]
        return render_template("profile.html", user=user, posted=posted, answered=answered,
                               bookmarks=bookmarks, activity_map=activity_map, streak=streak,
                               longest=longest, goal=goal, goal_done=goal_done)

    @app.route("/profile/goal", methods=["POST"])
    def profile_goal():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        db = get_db(app)
        db.execute(
            """INSERT INTO learning_goals(user_id,goal_type,target_count,period)
               VALUES(?,?,?,?) ON CONFLICT(user_id,period)
               DO UPDATE SET goal_type=excluded.goal_type,target_count=excluded.target_count""",
            (session["user_id"], request.form["goal_type"], int(request.form["target_count"]), "daily"),
        )
        db.commit()
        flash("学習目標を更新しました。")
        return redirect(url_for("profile"))

    @app.route("/groups")
    def groups():
        db = get_db(app)
        rows = db.execute(
            """SELECT g.*, u.display_name,
                      (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id=g.id) AS member_count
               FROM groups g JOIN users u ON u.id=g.creator_id
               WHERE g.visibility='public' ORDER BY g.created_at DESC"""
        ).fetchall()
        return render_template("groups.html", groups=rows)

    @app.route("/groups/new", methods=["GET", "POST"])
    def group_new():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if request.method == "POST":
            db = get_db(app)
            cursor = db.execute(
                """INSERT INTO groups(creator_id,name,purpose,subject,target_date,description,visibility)
                   VALUES(?,?,?,?,?,?,?)""",
                (session["user_id"], request.form["name"], request.form["purpose"],
                 request.form["subject"], request.form["target_date"],
                 request.form["description"], request.form["visibility"]),
            )
            db.execute(
                "INSERT INTO group_members(group_id,user_id) VALUES(?,?)",
                (cursor.lastrowid, session["user_id"]),
            )
            db.commit()
            flash("グループを作成しました。")
            return redirect(url_for("group_detail", group_id=cursor.lastrowid))
        return render_template("group_form.html")

    @app.route("/groups/<int:group_id>")
    def group_detail(group_id):
        db = get_db(app)
        group = db.execute(
            """SELECT g.*, u.display_name,
                      (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id=g.id) AS member_count
               FROM groups g JOIN users u ON u.id=g.creator_id WHERE g.id=?""",
            (group_id,),
        ).fetchone()
        if not group:
            return render_template("404.html"), 404
        member = False
        if session.get("user_id"):
            member = db.execute(
                "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
                (group_id, session["user_id"]),
            ).fetchone()
        if group["visibility"] == "private" and not member:
            return render_template("404.html"), 404
        problems = db.execute(
            """SELECT p.*, u.display_name FROM problems p JOIN users u ON u.id=p.user_id
               WHERE p.group_id=? ORDER BY p.created_at DESC""",
            (group_id,),
        ).fetchall()
        return render_template("group_detail.html", group=group, problems=problems,
                               member=bool(member))

    @app.route("/groups/<int:group_id>/join", methods=["POST"])
    def group_join(group_id):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        db = get_db(app)
        db.execute(
            "INSERT OR IGNORE INTO group_members(group_id,user_id) VALUES(?,?)",
            (group_id, session["user_id"]),
        )
        db.commit()
        flash("グループに参加しました。")
        return redirect(url_for("group_detail", group_id=group_id))

    return app


def record_activity(app, user_id, activity_type):
    db = get_db(app)
    db.execute(
        "INSERT INTO learning_activity(user_id,activity_type,activity_date) VALUES(?,?,?)",
        (user_id, activity_type, date.today().isoformat()),
    )
    db.commit()


def calculate_streak(app, user_id):
    db = get_db(app)
    rows = db.execute(
        """SELECT DISTINCT activity_date FROM learning_activity
           WHERE user_id=? ORDER BY activity_date DESC""",
        (user_id,),
    ).fetchall()
    days = {row["activity_date"] for row in rows}
    if not days:
        return 0, 0
    longest = 0
    current_longest = 0
    previous = None
    for day_text in sorted(days):
        day = date.fromisoformat(day_text)
        if previous is not None and day == previous + timedelta(days=1):
            current_longest += 1
        else:
            current_longest = 1
        longest = max(longest, current_longest)
        previous = day
    cursor = date.today()
    if cursor.isoformat() not in days:
        cursor = cursor - timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak, longest


def seed_demo_data(app):
    db = get_db(app)
    password = generate_password_hash("demo1234")
    cursor = db.execute(
        "INSERT INTO users(email,password_hash,display_name,bio,strong_subject) VALUES(?,?,?,?,?)",
        ("demo@example.com", password, "デモユーザー", "高校学習を楽しく続けたいです。", "数学"),
    )
    user_id = cursor.lastrowid
    group_cursor = db.execute(
        """INSERT INTO groups(creator_id,name,purpose,subject,target_date,description,visibility)
           VALUES(?,?,?,?,?,?,?)""",
        (user_id, "〇〇大学 合格目標", "共通テスト対策", "数学・英語",
         (date.today() + timedelta(days=180)).isoformat(),
         "毎日少しずつ問題を出し合う学習グループです。", "public"),
    )
    group_id = group_cursor.lastrowid
    db.execute("INSERT INTO group_members(group_id,user_id) VALUES(?,?)", (group_id, user_id))
    seed_problems = [
        ("一次関数のグラフ", "数学", "一次関数", "基礎",
         "y=2x+3について、x=4のときのyの値を求めなさい。",
         "11", "xに4を代入するとy=2×4+3=11です。"),
        ("英語の不規則変化", "英語", "不規則動詞", "基礎",
         "goの過去形を答えなさい。",
         "went", "goは不規則動詞で、過去形はwentです。"),
        ("光合成の場所", "理科", "生物", "基礎",
         "植物で光合成が行われる主な細胞小器官を答えなさい。",
         "葉緑体", "葉緑体では光合成に必要なクロロフィルを含みます。"),
    ]
    for title, subject, field, difficulty, body, answer, explanation in seed_problems:
        db.execute(
            """INSERT INTO problems(user_id,title,subject,field,difficulty,body,answer,explanation)
               VALUES(?,?,?,?,?,?,?,?)""",
            (user_id, title, subject, field, difficulty, body, answer, explanation),
        )
    for offset in range(6):
        activity_date = (date.today() - timedelta(days=offset)).isoformat()
        db.execute(
            "INSERT INTO learning_activity(user_id,activity_type,activity_date) VALUES(?,?,?)",
            (user_id, "answer", activity_date),
        )
    db.execute(
        "INSERT INTO learning_goals(user_id,goal_type,target_count,period) VALUES(?,?,?,?)",
        (user_id, "answer", 3, "daily"),
    )
    db.commit()


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5051")), debug=True)
