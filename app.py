import os
import hashlib
from datetime import datetime

import streamlit as st
import psycopg2
from psycopg2 import IntegrityError
from openai import OpenAI


# =========================================================
# 기본 설정
# =========================================================

APP_NAME = "데미's 릴스 대본 제작기"
DEFAULT_FREE_CREDITS = 30


def secret(name, default=""):
    """Streamlit Secrets 또는 환경변수에서 값을 가져옵니다."""
    try:
        value = st.secrets.get(name, "")
        if value:
            return str(value)
    except Exception:
        pass

    return os.getenv(name, default)


STUDENT_INVITE_CODE = secret("DEMI_STUDENT_INVITE_CODE", "")

st.set_page_config(
    page_title=APP_NAME,
    page_icon="🎬",
    layout="centered"
)

st.markdown(
    """
    <style>
    .block-container {
        max-width: 980px;
        padding-top: 1.5rem;
        padding-bottom: 4rem;
    }

    .credit-box {
        padding: 14px 18px;
        border-radius: 14px;
        background: rgba(128,128,128,0.08);
        margin: 10px 0 18px 0;
    }

    .stButton > button {
        font-weight: 700;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# =========================================================
# 비밀번호
# =========================================================

def hash_pw(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# =========================================================
# DB
# =========================================================

class DBConnection:
    def __init__(self, db_url):
        self.raw = psycopg2.connect(
            db_url,
            sslmode="require"
        )

    def cursor(self):
        return self.raw.cursor()

    def execute(self, sql, params=None):
        cur = self.raw.cursor()
        cur.execute(sql, params or ())
        return cur

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def conn():
    db_url = secret("SUPABASE_DB_URL", "").strip()

    if not db_url:
        raise RuntimeError(
            "SUPABASE_DB_URL이 Streamlit Secrets에 설정되지 않았습니다."
        )

    return DBConnection(db_url)


# =========================================================
# 관리자 계정 초기화
# =========================================================

def init_db():
    c = conn()

    admin_user = secret("DEMI_ADMIN_ID", "admin").strip()
    admin_pw = secret("DEMI_ADMIN_PASSWORD", "").strip()

    if not admin_pw:
        c.close()
        return

    cur = c.execute(
        "SELECT id FROM users WHERE username=%s",
        (admin_user,)
    )

    row = cur.fetchone()
    cur.close()

    if row:
        c.execute(
            """
            UPDATE users
            SET password_hash=%s,
                name='관리자',
                is_active=true,
                is_admin=true
            WHERE username=%s
            """,
            (hash_pw(admin_pw), admin_user)
        )

    else:
        c.execute(
            """
            INSERT INTO users
            (
                username,
                password_hash,
                name,
                credits,
                is_active,
                is_admin,
                created_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                admin_user,
                hash_pw(admin_pw),
                "관리자",
                999999,
                True,
                True,
                datetime.now()
            )
        )

    c.commit()
    c.close()


# =========================================================
# 사용자
# =========================================================

def get_user(username):
    c = conn()

    cur = c.execute(
        """
        SELECT
            id,
            username,
            password_hash,
            name,
            credits,
            is_active,
            is_admin,
            created_at
        FROM users
        WHERE username=%s
        """,
        (username,)
    )

    row = cur.fetchone()
    cur.close()
    c.close()

    return row


def user_by_id(uid):
    c = conn()

    cur = c.execute(
        """
        SELECT
            id,
            username,
            password_hash,
            name,
            credits,
            is_active,
            is_admin,
            created_at
        FROM users
        WHERE id=%s
        """,
        (uid,)
    )

    row = cur.fetchone()
    cur.close()
    c.close()

    return row


def create_user(username, password, name):
    c = conn()

    try:
        c.execute(
            """
            INSERT INTO users
            (
                username,
                password_hash,
                name,
                credits,
                is_active,
                is_admin,
                created_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                username.strip(),
                hash_pw(password),
                name.strip(),
                DEFAULT_FREE_CREDITS,
                True,
                False,
                datetime.now()
            )
        )

        c.commit()
        return True, None

    except IntegrityError:
        c.rollback()
        return False, "이미 사용 중인 아이디입니다."

    finally:
        c.close()


def change_credits(uid, delta):
    c = conn()

    c.execute(
        """
        UPDATE users
        SET credits = GREATEST(0, credits + %s)
        WHERE id=%s
        """,
        (delta, uid)
    )

    c.commit()
    c.close()


def deduct_one_credit(uid):
    """
    크레딧이 1 이상일 때만 1 차감합니다.
    """
    c = conn()

    cur = c.execute(
        """
        UPDATE users
        SET credits = credits - 1
        WHERE id=%s
          AND credits > 0
        RETURNING credits
        """,
        (uid,)
    )

    row = cur.fetchone()

    if row:
        c.commit()
        cur.close()
        c.close()
        return True

    c.rollback()
    cur.close()
    c.close()

    return False


# =========================================================
# 생성 기록
# =========================================================

def log_generation(
    uid,
    product_name,
    content_type,
    duration="",
    target_name="",
    hook_type="",
    script_text=""
):
    c = conn()

    c.execute(
        """
        INSERT INTO generations
        (
            user_id,
            product_name,
            content_type,
            created_at,
            credits_used,
            duration,
            target_name,
            hook_type,
            script_text
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            uid,
            product_name,
            content_type,
            datetime.now(),
            1,
            duration,
            target_name,
            hook_type,
            script_text
        )
    )

    c.commit()
    c.close()


# =========================================================
# 관리자용
# =========================================================

def get_students():
    c = conn()

    cur = c.execute(
        """
        SELECT
            id,
            username,
            name,
            credits,
            is_active,
            created_at
        FROM users
        WHERE is_admin=false
        ORDER BY created_at DESC
        """
    )

    rows = cur.fetchall()

    cur.close()
    c.close()

    return rows


def set_user_active(uid, active):
    c = conn()

    c.execute(
        """
        UPDATE users
        SET is_active=%s
        WHERE id=%s
        """,
        (active, uid)
    )

    c.commit()
    c.close()


def get_recent_generations(limit=30):
    c = conn()

    cur = c.execute(
        """
        SELECT
            g.created_at,
            u.username,
            u.name,
            g.product_name,
            g.content_type,
            g.duration
        FROM generations g
        JOIN users u
          ON u.id = g.user_id
        ORDER BY g.created_at DESC
        LIMIT %s
        """,
        (limit,)
    )

    rows = cur.fetchall()

    cur.close()
    c.close()

    return rows


# =========================================================
# 출력 내용 분리
# =========================================================

def get_section(text, start, end=None):
    if start not in text:
        return ""

    content = text.split(start, 1)[1]

    if end and end in content:
        content = content.split(end, 1)[0]

    return content.strip()


# =========================================================
# 시작
# =========================================================

try:
    init_db()

except Exception as e:
    st.error("데이터베이스 연결 중 오류가 발생했습니다.")
    st.code(str(e))
    st.stop()


if "user_id" not in st.session_state:
    st.session_state.user_id = None


def logout():
    st.session_state.user_id = None
    st.rerun()


# =========================================================
# 로그인 / 회원가입
# =========================================================

if not st.session_state.user_id:

    st.title("🎬 데미's 릴스 대본 제작기")

    st.caption(
        "주제나 상품만 입력하면 후킹부터 릴스 대본, "
        "인스타 본문까지 한 번에 만들어드려요 ✨"
    )

    login_tab, join_tab = st.tabs(
        ["로그인", "수강생 가입"]
    )

    with login_tab:

        login_id = st.text_input(
            "아이디",
            key="login_id"
        )

        login_pw = st.text_input(
            "비밀번호",
            type="password",
            key="login_pw"
        )

        if st.button(
            "로그인",
            type="primary",
            use_container_width=True
        ):

            user = get_user(login_id.strip())

            if not user or user[2] != hash_pw(login_pw):
                st.error("아이디 또는 비밀번호를 확인해주세요.")

            elif not user[5]:
                st.error("현재 이용이 정지된 계정입니다.")

            else:
                st.session_state.user_id = user[0]
                st.rerun()

    with join_tab:

        name = st.text_input(
            "이름",
            key="join_name"
        )

        join_id = st.text_input(
            "아이디",
            key="join_id"
        )

        pw1 = st.text_input(
            "비밀번호",
            type="password",
            key="join_pw1"
        )

        pw2 = st.text_input(
            "비밀번호 확인",
            type="password",
            key="join_pw2"
        )

        invite_code = st.text_input(
            "수강생 초대코드",
            type="password",
            key="invite_code"
        )

        if st.button(
            "수강생 가입",
            use_container_width=True
        ):

            if not name.strip():
                st.error("이름을 입력해주세요.")

            elif not join_id.strip():
                st.error("아이디를 입력해주세요.")

            elif len(pw1) < 4:
                st.error("비밀번호는 4자리 이상 입력해주세요.")

            elif pw1 != pw2:
                st.error("비밀번호가 서로 다릅니다.")

            elif STUDENT_INVITE_CODE and invite_code != STUDENT_INVITE_CODE:
                st.error("수강생 초대코드가 올바르지 않습니다.")

            else:
                ok, msg = create_user(
                    join_id,
                    pw1,
                    name
                )

                if ok:
                    st.success(
                        f"가입 완료! 기본 {DEFAULT_FREE_CREDITS}크레딧이 지급되었습니다."
                    )

                else:
                    st.error(msg)

    st.stop()


# =========================================================
# 로그인 후
# =========================================================

user = user_by_id(st.session_state.user_id)

if not user:
    logout()

uid = user[0]
username = user[1]
name = user[3]
credits = user[4]
is_active = user[5]
is_admin = user[6]

if not is_active and not is_admin:
    st.error("현재 이용이 정지된 계정입니다.")

    if st.button("로그아웃"):
        logout()

    st.stop()


# =========================================================
# 상단
# =========================================================

c1, c2 = st.columns([4, 1])

with c1:
    st.title("🎬 데미's 릴스 대본 제작기")

with c2:
    if st.button("로그아웃"):
        logout()


if is_admin:
    st.success("👑 관리자 계정으로 로그인했습니다.")

else:
    st.markdown(
        f"""
        <div class="credit-box">
        👤 <b>{name}</b>님 &nbsp;&nbsp; | &nbsp;&nbsp;
        💎 현재 크레딧 <b>{credits}</b>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.caption(
        "릴스 콘텐츠 1회 생성 시 1크레딧이 사용됩니다."
    )


# =========================================================
# 관리자 화면
# =========================================================

if is_admin:

    create_tab, student_tab, history_tab = st.tabs(
        [
            "🎬 릴스 제작",
            "👥 수강생 관리",
            "📋 사용 기록"
        ]
    )

else:

    create_tab = st.container()
    student_tab = None
    history_tab = None


# =========================================================
# 릴스 생성
# =========================================================

with create_tab:

    st.subheader("✨ 릴스 콘텐츠 만들기")

    with st.form("reels_form"):

        topic = st.text_input(
            "📌 주제 / 상품명",
            placeholder="예: 자석식 메이크업 가방"
        )

        content_type = st.selectbox(
            "🎯 콘텐츠 유형",
            [
                "조회수형",
                "정보형",
                "공감형",
                "광고형",
                "CPA형",
                "공동구매형",
                "후기형"
            ]
        )

        target = st.text_input(
            "👤 타깃",
            placeholder="예: 20~30대 여성"
        )

        duration = st.selectbox(
            "⏱ 영상 길이",
            [
                "15초",
                "30초",
                "45초",
                "60초"
            ]
        )

        tone = st.selectbox(
            "💬 말투",
            [
                "친구에게 말하듯 자연스럽게",
                "강한 후킹",
                "공감 가득하게",
                "깔끔하고 전문적으로",
                "유머러스하게"
            ]
        )

        extra = st.text_area(
            "✍️ 추가로 넣고 싶은 내용",
            placeholder=(
                "예: 댓글에 '정보' 남기도록 유도해줘 / "
                "가격은 언급하지 마"
            )
        )

        submitted = st.form_submit_button(
            "✨ 릴스 콘텐츠 만들기",
            type="primary",
            use_container_width=True
        )


    if submitted:

        if not topic.strip():

            st.warning("주제나 상품명을 입력해주세요.")

        elif not is_admin and credits <= 0:

            st.error(
                "크레딧이 부족합니다. 관리자에게 충전을 요청해주세요."
            )

        else:

            api_key = secret("OPENAI_API_KEY", "").strip()

            if not api_key:

                st.error(
                    "OPENAI_API_KEY가 Streamlit Secrets에 설정되지 않았습니다."
                )

            else:

                prompt = f"""
너는 인스타그램 릴스 전문 콘텐츠 기획자이자
숏폼 카피라이터야.

아래 정보를 바탕으로 실제 촬영해서 사용할 수 있는
한국어 릴스 콘텐츠를 작성해줘.

[입력 정보]

주제 또는 상품:
{topic}

콘텐츠 유형:
{content_type}

타깃:
{target if target else "일반 인스타그램 사용자"}

영상 길이:
{duration}

말투:
{tone}

추가 요청:
{extra if extra else "없음"}


[작성 원칙]

1. 첫 1~3초 안에 시청자가 스크롤을 멈출 수 있어야 한다.

2. 후킹은 서로 다른 방향으로 3개 제안한다.

3. 너무 광고처럼 느껴지는 표현은 피한다.

4. 실제 사람이 말하는 것처럼 자연스러운 한국어를 사용한다.

5. 한 문장은 짧고 영상 자막으로 쓰기 쉽게 만든다.

6. 콘텐츠 흐름은 가능하면
   문제 → 공감 → 궁금증 → 해결 → 행동유도
   구조를 활용한다.

7. 상품의 확인되지 않은 기능이나 효능을 임의로 만들지 않는다.

8. 제공되지 않은 가격, 할인율, 성분, 인증, 판매량 등의
   사실을 만들어내지 않는다.

9. CTA는 댓글, 저장, 공유, 프로필 확인 등
   콘텐츠 성격에 가장 적합한 방식으로 작성한다.

10. 인스타 본문은 릴스 대본을 그대로 복사하지 않는다.
    게시물용 문장으로 별도로 작성한다.

11. 해시태그는 관련성이 높은 것만 8~12개 작성한다.

12. 영상 길이에 맞춰 대본 분량을 조절한다.


반드시 아래 형식을 그대로 사용해.

[HOOKS]
1.
2.
3.

[SCRIPT]
실제로 말할 릴스 전체 대본

[SUBTITLES]
영상에 넣을 핵심 자막을 한 줄씩 작성

[CTA]
1.
2.
3.

[CAPTION]
인스타그램 게시글 본문

[HASHTAGS]
해시태그
"""

                try:

                    with st.spinner(
                        "릴스 콘텐츠를 만들고 있어요... ✨"
                    ):

                        client = OpenAI(api_key=api_key)

                        response = client.responses.create(
                            model="gpt-5-mini",
                            input=prompt
                        )

                        result = response.output_text


                    # AI 호출 성공 후 크레딧 차감
                    if not is_admin:

                        paid = deduct_one_credit(uid)

                        if not paid:
                            st.error(
                                "크레딧이 부족하여 결과를 저장하지 못했습니다."
                            )
                            st.stop()


                    log_generation(
                        uid,
                        topic.strip(),
                        content_type,
                        duration,
                        target,
                        tone,
                        result
                    )


                    hooks = get_section(
                        result,
                        "[HOOKS]",
                        "[SCRIPT]"
                    )

                    script = get_section(
                        result,
                        "[SCRIPT]",
                        "[SUBTITLES]"
                    )

                    subtitles = get_section(
                        result,
                        "[SUBTITLES]",
                        "[CTA]"
                    )

                    cta = get_section(
                        result,
                        "[CTA]",
                        "[CAPTION]"
                    )

                    caption = get_section(
                        result,
                        "[CAPTION]",
                        "[HASHTAGS]"
                    )

                    hashtags = get_section(
                        result,
                        "[HASHTAGS]"
                    )


                    st.success("콘텐츠가 완성됐어요! 🎉")

                    if not is_admin:
                        new_user = user_by_id(uid)

                        st.info(
                            f"💎 남은 크레딧: {new_user[4]}"
                        )

                    st.divider()

                    st.subheader("🔥 3초 후킹")
                    st.code(
                        hooks or result,
                        language=None
                    )

                    st.subheader("🎬 릴스 대본")
                    st.code(
                        script,
                        language=None
                    )

                    st.subheader("📱 화면 자막")
                    st.code(
                        subtitles,
                        language=None
                    )

                    st.subheader("💬 CTA")
                    st.code(
                        cta,
                        language=None
                    )

                    st.subheader("✍️ 인스타 본문")
                    st.code(
                        caption,
                        language=None
                    )

                    st.subheader("#️⃣ 해시태그")
                    st.code(
                        hashtags,
                        language=None
                    )


                except Exception as e:

                    st.error(
                        "AI 호출 중 오류가 발생했습니다. "
                        "크레딧은 차감되지 않았습니다."
                    )

                    st.code(str(e))


# =========================================================
# 수강생 관리
# =========================================================

if is_admin and student_tab is not None:

    with student_tab:

        st.subheader("👥 수강생 관리")

        students = get_students()

        if not students:

            st.info("가입한 수강생이 없습니다.")

        for r in students:

            uid2 = r[0]
            username2 = r[1]
            name2 = r[2]
            credits2 = r[3]
            active2 = r[4]

            status_text = "이용중" if active2 else "정지"

            with st.expander(
                f"{name2} · @{username2} · "
                f"{credits2}크레딧 · {status_text}"
            ):

                c1, c2, c3 = st.columns(3)

                add = c1.number_input(
                    "크레딧 지급",
                    min_value=1,
                    value=10,
                    key=f"add_{uid2}"
                )

                if c1.button(
                    "지급",
                    key=f"give_{uid2}"
                ):
                    change_credits(
                        uid2,
                        int(add)
                    )
                    st.success(
                        f"{add}크레딧을 지급했습니다."
                    )
                    st.rerun()


                minus = c2.number_input(
                    "크레딧 차감",
                    min_value=1,
                    value=10,
                    key=f"minus_amount_{uid2}"
                )

                if c2.button(
                    "차감",
                    key=f"minus_{uid2}"
                ):
                    change_credits(
                        uid2,
                        -int(minus)
                    )
                    st.success(
                        f"{minus}크레딧을 차감했습니다."
                    )
                    st.rerun()


                active_label = (
                    "이용 정지"
                    if active2
                    else "이용 재개"
                )

                if c3.button(
                    active_label,
                    key=f"active_{uid2}"
                ):

                    set_user_active(
                        uid2,
                        not active2
                    )

                    st.rerun()


# =========================================================
# 사용 기록
# =========================================================

if is_admin and history_tab is not None:

    with history_tab:

        st.subheader("📋 최근 릴스 생성 기록")

        try:

            history = get_recent_generations(50)

            if not history:

                st.info("아직 생성 기록이 없습니다.")

            else:

                for h in history:

                    created = h[0]
                    username3 = h[1]
                    name3 = h[2]
                    product = h[3]
                    ctype = h[4]
                    dur = h[5]

                    st.write(
                        f"**{name3} (@{username3})** · "
                        f"{product} · {ctype} · {dur}"
                    )

                    st.caption(
                        created.strftime(
                            "%Y-%m-%d %H:%M"
                        )
                    )

                    st.divider()

        except Exception as e:

            st.warning(
                "기존 generations 테이블 구조에 따라 "
                "사용 기록 화면은 추가 조정이 필요할 수 있습니다."
            )

            st.code(str(e))
