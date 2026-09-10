import os
import hashlib
import secrets
from datetime import datetime, timedelta

import streamlit as st
import psycopg2
from psycopg2 import IntegrityError, OperationalError, InterfaceError
from psycopg2.pool import ThreadedConnectionPool
from openai import OpenAI
from streamlit_cookies_controller import CookieController


# =========================================================
# 기본 설정
# =========================================================
APP_NAME = "데미's 릴스 대본 제작기"
DEFAULT_FREE_CREDITS = 30
CREDIT_COST_PER_GENERATION = 1

LOGIN_COOKIE_NAME = "demi_reels_login"
LOGIN_DAYS = 30

DB_POOL_MIN = 1
DB_POOL_MAX = 5

st.set_page_config(
    page_title=APP_NAME,
    page_icon="🎬",
    layout="centered",
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
        padding: 16px 20px;
        border-radius: 15px;
        background: rgba(128,128,128,0.08);
        margin: 10px 0 15px 0;
        font-size: 17px;
    }

    .bank-box {
        padding: 20px;
        border-radius: 15px;
        background: rgba(255, 165, 0, 0.10);
        border: 1px solid rgba(255, 165, 0, 0.30);
        margin: 15px 0;
        font-size: 17px;
        line-height: 1.8;
    }

    .price-box {
        padding: 18px;
        border-radius: 15px;
        border: 1px solid rgba(128,128,128,0.25);
        margin-top: 10px;
        line-height: 1.8;
    }

    .stButton > button {
        font-weight: 700;
        border-radius: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# Secrets / 환경설정
# =========================================================
def secret(name, default=""):
    try:
        value = st.secrets.get(name, "")
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv(name, default)


def get_db_url():
    db_url = secret("SUPABASE_DB_URL", "").strip()
    if not db_url:
        raise RuntimeError("SUPABASE_DB_URL이 설정되지 않았습니다.")
    return db_url


STUDENT_INVITE_CODE = secret("DEMI_STUDENT_INVITE_CODE", "")
BANK_NAME = secret("RECHARGE_BANK_NAME", "")
BANK_ACCOUNT = secret("RECHARGE_ACCOUNT", "")
BANK_HOLDER = secret("RECHARGE_ACCOUNT_HOLDER", "")
OPENAI_MODEL = secret("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"


# =========================================================
# 해시
# =========================================================
def hash_pw(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# =========================================================
# DB 연결 풀
# 핵심 최적화 1: 쿼리마다 새 PostgreSQL 연결을 만들지 않음
# =========================================================
@st.cache_resource(show_spinner=False)
def get_db_pool(db_url):
    return ThreadedConnectionPool(
        DB_POOL_MIN,
        DB_POOL_MAX,
        dsn=db_url,
        sslmode="require",
        connect_timeout=8,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )


def _run_db(operation, commit=False):
    """DB 작업 공통 실행기.

    - 연결 풀에서 연결을 빌림
    - 읽기 작업은 transaction 상태를 rollback으로 정리
    - 쓰기 작업은 commit
    - 끊어진 연결은 한 번 새 연결로 재시도
    """
    pool = get_db_pool(get_db_url())

    for attempt in range(2):
        conn = None
        broken = False

        try:
            conn = pool.getconn()
            result = operation(conn)

            if commit:
                conn.commit()
            else:
                # SELECT 뒤 idle in transaction 상태를 남기지 않기 위해 정리
                conn.rollback()

            return result

        except (OperationalError, InterfaceError):
            broken = True
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass

            if attempt == 1:
                raise

        except Exception:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise

        finally:
            if conn is not None:
                try:
                    pool.putconn(
                        conn,
                        close=broken or bool(conn.closed),
                    )
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass


def fetch_one(query, params=()):
    def operation(conn):
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()

    return _run_db(operation)


def fetch_all(query, params=()):
    def operation(conn):
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()

    return _run_db(operation)


def execute_write(query, params=()):
    def operation(conn):
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.rowcount

    return _run_db(operation, commit=True)


# =========================================================
# DB 최초 초기화
# 핵심 최적화 2: Streamlit rerun마다 CREATE TABLE / 관리자 UPDATE 안 함
# 서버 프로세스 시작 시 1번만 실행
# =========================================================
@st.cache_resource(show_spinner=False)
def initialize_database(db_url, admin_id, admin_password_hash):
    pool = get_db_pool(db_url)
    conn = pool.getconn()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    credits INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    is_admin BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reels_generations (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    product_name TEXT,
                    content_type TEXT,
                    duration TEXT,
                    target_name TEXT,
                    tone TEXT,
                    script_text TEXT,
                    credits_used INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reels_recharge_requests (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    package_name TEXT NOT NULL,
                    credits INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    depositor_name TEXT NOT NULL,
                    status TEXT DEFAULT '대기',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS login_tokens (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT UNIQUE NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # 자주 조회하는 컬럼 인덱스
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_login_tokens_expires
                ON login_tokens(expires_at)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recharge_user_status
                ON reels_recharge_requests(user_id, status)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recharge_status_created
                ON reels_recharge_requests(status, created_at)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_generations_created
                ON reels_generations(created_at DESC)
                """
            )

            if admin_password_hash:
                cur.execute(
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
                    ON CONFLICT (username)
                    DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        name = '관리자',
                        is_active = TRUE,
                        is_admin = TRUE
                    """,
                    (
                        admin_id,
                        admin_password_hash,
                        "관리자",
                        999999,
                        True,
                        True,
                        datetime.now(),
                    ),
                )

        conn.commit()
        return True

    except Exception:
        conn.rollback()
        raise

    finally:
        pool.putconn(conn)


# =========================================================
# 사용자 / 로그인
# =========================================================
def get_user(username):
    return fetch_one(
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
        (username,),
    )


def get_user_by_id(uid):
    return fetch_one(
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
        (uid,),
    )


def create_user(username, password, name):
    def operation(conn):
        with conn.cursor() as cur:
            cur.execute(
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
                    datetime.now(),
                ),
            )

    try:
        _run_db(operation, commit=True)
        return True, None
    except IntegrityError:
        return False, "이미 사용 중인 아이디입니다."


def change_credits(uid, amount):
    execute_write(
        """
        UPDATE users
        SET credits = GREATEST(0, credits + %s)
        WHERE id=%s
        """,
        (amount, uid),
    )


def create_login_token(uid):
    token = secrets.token_urlsafe(32)
    token_hash_value = hash_token(token)
    now = datetime.now()
    expires_at = now + timedelta(days=LOGIN_DAYS)

    def operation(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM login_tokens
                WHERE user_id=%s
                   OR expires_at < %s
                """,
                (uid, now),
            )

            cur.execute(
                """
                INSERT INTO login_tokens
                (
                    user_id,
                    token_hash,
                    expires_at,
                    created_at
                )
                VALUES (%s,%s,%s,%s)
                """,
                (uid, token_hash_value, expires_at, now),
            )

    _run_db(operation, commit=True)
    return token


def get_user_from_token(token):
    if not token:
        return None

    return fetch_one(
        """
        SELECT
            u.id,
            u.username,
            u.password_hash,
            u.name,
            u.credits,
            u.is_active,
            u.is_admin,
            u.created_at
        FROM login_tokens lt
        JOIN users u
            ON u.id = lt.user_id
        WHERE lt.token_hash=%s
          AND lt.expires_at > %s
        """,
        (hash_token(token), datetime.now()),
    )


def delete_login_token(token):
    if not token:
        return

    execute_write(
        """
        DELETE FROM login_tokens
        WHERE token_hash=%s
        """,
        (hash_token(token),),
    )


# =========================================================
# 릴스 생성 / 기록
# 핵심 최적화 3: 크레딧 차감 + 생성 기록 저장을 DB 1회 transaction으로 처리
# =========================================================
def save_generation_and_consume_credit(
    uid,
    is_admin,
    product_name,
    content_type,
    duration,
    target,
    tone,
    result,
):
    def operation(conn):
        with conn.cursor() as cur:
            new_credits = None

            if not is_admin:
                cur.execute(
                    """
                    UPDATE users
                    SET credits = credits - %s
                    WHERE id=%s
                      AND credits >= %s
                    RETURNING credits
                    """,
                    (
                        CREDIT_COST_PER_GENERATION,
                        uid,
                        CREDIT_COST_PER_GENERATION,
                    ),
                )

                row = cur.fetchone()
                if not row:
                    raise ValueError("CREDIT_SHORTAGE")
                new_credits = row[0]

            cur.execute(
                """
                INSERT INTO reels_generations
                (
                    user_id,
                    product_name,
                    content_type,
                    duration,
                    target_name,
                    tone,
                    script_text,
                    credits_used,
                    created_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    uid,
                    product_name,
                    content_type,
                    duration,
                    target,
                    tone,
                    result,
                    CREDIT_COST_PER_GENERATION,
                    datetime.now(),
                ),
            )

            return new_credits

    try:
        new_credits = _run_db(operation, commit=True)
        return True, new_credits
    except ValueError as exc:
        if str(exc) == "CREDIT_SHORTAGE":
            return False, None
        raise


# =========================================================
# 충전
# =========================================================
def create_recharge_request(uid, package_name, credits, amount, depositor):
    def operation(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM reels_recharge_requests
                WHERE user_id=%s
                  AND status='대기'
                LIMIT 1
                """,
                (uid,),
            )

            if cur.fetchone():
                return False

            cur.execute(
                """
                INSERT INTO reels_recharge_requests
                (
                    user_id,
                    package_name,
                    credits,
                    amount,
                    depositor_name,
                    status,
                    created_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    uid,
                    package_name,
                    credits,
                    amount,
                    depositor.strip(),
                    "대기",
                    datetime.now(),
                ),
            )

            return True

    ok = _run_db(operation, commit=True)

    if not ok:
        return False, "이미 처리 대기 중인 충전 요청이 있습니다."

    return True, "충전 요청이 접수되었습니다."


def get_my_recharge_requests(uid):
    return fetch_all(
        """
        SELECT
            package_name,
            credits,
            amount,
            depositor_name,
            status,
            created_at
        FROM reels_recharge_requests
        WHERE user_id=%s
        ORDER BY created_at DESC
        LIMIT 10
        """,
        (uid,),
    )


def get_pending_recharges():
    return fetch_all(
        """
        SELECT
            r.id,
            r.user_id,
            u.username,
            u.name,
            r.package_name,
            r.credits,
            r.amount,
            r.depositor_name,
            r.status,
            r.created_at
        FROM reels_recharge_requests r
        JOIN users u
            ON u.id = r.user_id
        WHERE r.status='대기'
        ORDER BY r.created_at ASC
        """
    )


def approve_recharge(request_id):
    def operation(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, credits, status
                FROM reels_recharge_requests
                WHERE id=%s
                FOR UPDATE
                """,
                (request_id,),
            )

            request = cur.fetchone()
            if not request:
                return False, "요청을 찾을 수 없습니다."

            uid, credits, status = request

            if status != "대기":
                return False, "이미 처리된 요청입니다."

            cur.execute(
                """
                UPDATE users
                SET credits = credits + %s
                WHERE id=%s
                """,
                (credits, uid),
            )

            cur.execute(
                """
                UPDATE reels_recharge_requests
                SET status='승인', processed_at=%s
                WHERE id=%s
                """,
                (datetime.now(), request_id),
            )

            return True, f"{credits}크레딧 지급 완료!"

    try:
        return _run_db(operation, commit=True)
    except Exception as exc:
        return False, str(exc)


def reject_recharge(request_id):
    execute_write(
        """
        UPDATE reels_recharge_requests
        SET status='거절', processed_at=%s
        WHERE id=%s
          AND status='대기'
        """,
        (datetime.now(), request_id),
    )


# =========================================================
# 관리자
# =========================================================
def get_students():
    return fetch_all(
        """
        SELECT
            id,
            username,
            name,
            credits,
            is_active,
            created_at
        FROM users
        WHERE is_admin=FALSE
        ORDER BY created_at DESC
        """
    )


def set_user_active(uid, active):
    execute_write(
        """
        UPDATE users
        SET is_active=%s
        WHERE id=%s
        """,
        (active, uid),
    )


def get_generation_history():
    return fetch_all(
        """
        SELECT
            g.created_at,
            u.username,
            u.name,
            g.product_name,
            g.content_type,
            g.duration
        FROM reels_generations g
        JOIN users u
            ON u.id = g.user_id
        ORDER BY g.created_at DESC
        LIMIT 50
        """
    )


# =========================================================
# OpenAI
# 핵심 최적화 4: 클라이언트 객체 재사용
# =========================================================
@st.cache_resource(show_spinner=False)
def get_openai_client(api_key):
    return OpenAI(
        api_key=api_key,
        timeout=90.0,
        max_retries=1,
    )


# =========================================================
# 결과 파싱
# =========================================================
def get_section(text, start, end=None):
    if start not in text:
        return ""

    content = text.split(start, 1)[1]

    if end and end in content:
        content = content.split(end, 1)[0]

    return content.strip()


def parse_generation(result):
    return {
        "hooks": get_section(result, "[HOOKS]", "[SCRIPT]"),
        "script": get_section(result, "[SCRIPT]", "[SUBTITLES]"),
        "subtitles": get_section(result, "[SUBTITLES]", "[CTA]"),
        "cta": get_section(result, "[CTA]", "[CAPTION]"),
        "caption": get_section(result, "[CAPTION]", "[HASHTAGS]"),
        "hashtags": get_section(result, "[HASHTAGS]"),
    }


def show_generation_result(data):
    if not data:
        return

    st.success("콘텐츠가 완성됐어요! 🎉")

    st.subheader("🔥 3초 후킹 3개")
    st.code(data.get("hooks", ""))

    st.subheader("🎬 릴스 대본")
    st.code(data.get("script", ""))

    st.subheader("📱 화면 자막")
    st.code(data.get("subtitles", ""))

    st.subheader("💬 CTA")
    st.code(data.get("cta", ""))

    st.subheader("✍️ 인스타 본문")
    st.code(data.get("caption", ""))

    st.subheader("#️⃣ 해시태그")
    st.code(data.get("hashtags", ""))


# =========================================================
# 앱 최초 DB 준비
# =========================================================
try:
    admin_id = secret("DEMI_ADMIN_ID", "admin").strip()
    admin_password = secret("DEMI_ADMIN_PASSWORD", "").strip()
    admin_password_hash = hash_pw(admin_password) if admin_password else ""

    initialize_database(
        get_db_url(),
        admin_id,
        admin_password_hash,
    )

except Exception as exc:
    st.error("데이터베이스 연결 중 오류가 발생했습니다.")
    st.code(str(exc))
    st.stop()


# =========================================================
# 로그인 상태
# =========================================================
controller = CookieController()

if "user_id" not in st.session_state:
    st.session_state.user_id = None

if "last_generation" not in st.session_state:
    st.session_state.last_generation = None


if not st.session_state.user_id:
    try:
        saved_token = controller.get(LOGIN_COOKIE_NAME)

        if saved_token:
            saved_user = get_user_from_token(saved_token)

            if saved_user and saved_user[5]:
                st.session_state.user_id = saved_user[0]

    except Exception:
        pass


def logout():
    try:
        token = controller.get(LOGIN_COOKIE_NAME)

        if token:
            delete_login_token(token)

        controller.remove(LOGIN_COOKIE_NAME)

    except Exception:
        pass

    st.session_state.user_id = None
    st.session_state.last_generation = None
    st.rerun()


# =========================================================
# 로그인 / 가입 화면
# =========================================================
if not st.session_state.user_id:
    st.title("🎬 데미's 릴스 대본 제작기")
    st.caption(
        "주제나 상품만 입력하면 후킹부터 릴스 대본, "
        "인스타 본문까지 한 번에 ✨"
    )

    login_tab, signup_tab = st.tabs(["🔐 로그인", "✨ 수강생 가입"])

    with login_tab:
        login_id = st.text_input("아이디", key="login_id")
        login_pw = st.text_input(
            "비밀번호",
            type="password",
            key="login_pw",
        )

        if st.button(
            "로그인",
            type="primary",
            use_container_width=True,
        ):
            user = get_user(login_id.strip())

            if not user or user[2] != hash_pw(login_pw):
                st.error("아이디 또는 비밀번호를 확인해주세요.")

            elif not user[5]:
                st.error("현재 이용이 정지된 계정입니다.")

            else:
                login_token = create_login_token(user[0])

                controller.set(
                    LOGIN_COOKIE_NAME,
                    login_token,
                    max_age=LOGIN_DAYS * 24 * 60 * 60,
                )

                st.session_state.user_id = user[0]
                st.rerun()

    with signup_tab:
        signup_name = st.text_input("이름", key="signup_name")
        signup_id = st.text_input("아이디", key="signup_id")
        signup_pw1 = st.text_input(
            "비밀번호",
            type="password",
            key="signup_pw1",
        )
        signup_pw2 = st.text_input(
            "비밀번호 확인",
            type="password",
            key="signup_pw2",
        )
        invite_code = st.text_input(
            "수강생 초대코드",
            type="password",
            key="invite_code",
        )

        if st.button("수강생 가입", use_container_width=True):
            if not signup_name.strip():
                st.error("이름을 입력해주세요.")

            elif not signup_id.strip():
                st.error("아이디를 입력해주세요.")

            elif len(signup_pw1) < 4:
                st.error("비밀번호는 4자리 이상 입력해주세요.")

            elif signup_pw1 != signup_pw2:
                st.error("비밀번호가 서로 다릅니다.")

            elif STUDENT_INVITE_CODE and invite_code != STUDENT_INVITE_CODE:
                st.error("수강생 초대코드가 올바르지 않습니다.")

            else:
                ok, msg = create_user(
                    signup_id,
                    signup_pw1,
                    signup_name,
                )

                if ok:
                    st.success(
                        f"가입 완료! {DEFAULT_FREE_CREDITS}크레딧이 지급되었습니다."
                    )
                else:
                    st.error(msg)

    st.stop()


# =========================================================
# 로그인 후 사용자 정보
# pooled connection이라 이 1회 조회는 매우 가볍게 유지
# =========================================================
user = get_user_by_id(st.session_state.user_id)

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


top1, top2 = st.columns([5, 1])

with top1:
    st.title("🎬 데미's 릴스 대본 제작기")

with top2:
    if st.button("로그아웃"):
        logout()


# =========================================================
# 화면 렌더 함수
# 핵심 최적화 5: st.tabs 대신 선택한 메뉴만 실제로 실행
# =========================================================
def render_create_page():
    st.subheader("✨ 릴스 콘텐츠 만들기")

    with st.form("reels_form"):
        topic = st.text_input(
            "📌 주제 / 상품명",
            placeholder="예: 자석식 메이크업 가방",
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
                "후기형",
            ],
        )

        target = st.text_input(
            "👤 타깃",
            placeholder="예: 20~30대 여성",
        )

        duration = st.selectbox(
            "⏱ 영상 길이",
            ["15초", "30초", "45초", "60초"],
        )

        tone = st.selectbox(
            "💬 말투",
            [
                "친구에게 말하듯 자연스럽게",
                "강한 후킹",
                "공감 가득하게",
                "깔끔하고 전문적으로",
                "유머러스하게",
            ],
        )

        extra = st.text_area("✍️ 추가 요청사항")

        submitted = st.form_submit_button(
            "✨ 릴스 콘텐츠 만들기",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if not topic.strip():
            st.warning("주제나 상품명을 입력해주세요.")

        elif not is_admin and credits < CREDIT_COST_PER_GENERATION:
            st.error("크레딧이 부족합니다.")

        else:
            api_key = secret("OPENAI_API_KEY", "").strip()

            if not api_key:
                st.error("OPENAI_API_KEY가 설정되지 않았습니다.")

            else:
                prompt = f"""
너는 인스타그램 릴스 전문 콘텐츠 기획자야.

주제/상품:
{topic}

콘텐츠 유형:
{content_type}

타깃:
{target}

영상 길이:
{duration}

말투:
{tone}

추가 요청:
{extra}

아래 형식으로 작성해.

[HOOKS]
3초 후킹 3개

[SCRIPT]
릴스 전체 대본

[SUBTITLES]
화면 자막

[CTA]
CTA 3개

[CAPTION]
인스타그램 본문

[HASHTAGS]
관련 해시태그 8~12개
"""

                try:
                    with st.spinner("릴스 콘텐츠를 만들고 있어요... ✨"):
                        client = get_openai_client(api_key)
                        response = client.responses.create(
                            model=OPENAI_MODEL,
                            input=prompt,
                        )
                        result = response.output_text

                    ok, new_credits = save_generation_and_consume_credit(
                        uid=uid,
                        is_admin=is_admin,
                        product_name=topic.strip(),
                        content_type=content_type,
                        duration=duration,
                        target=target,
                        tone=tone,
                        result=result,
                    )

                    if not ok:
                        st.error(
                            "크레딧이 부족합니다. "
                            "다른 기기에서 사용했거나 잔액이 변경되었을 수 있습니다."
                        )
                        return

                    parsed = parse_generation(result)
                    st.session_state.last_generation = parsed
                    show_generation_result(parsed)

                    if not is_admin and new_credits is not None:
                        st.caption(f"💎 생성 후 남은 크레딧: {new_credits}")

                except Exception as exc:
                    st.error("AI 생성 중 오류가 발생했습니다.")
                    st.code(str(exc))

    elif st.session_state.last_generation:
        st.divider()
        st.caption("최근 생성 결과")
        show_generation_result(st.session_state.last_generation)


def render_recharge_page():
    st.subheader("💳 크레딧 충전")
    st.write("아래 계좌로 먼저 입금한 뒤 충전 요청을 보내주세요.")

    if BANK_NAME and BANK_ACCOUNT and BANK_HOLDER:
        st.markdown(
            f"""
            <div class="bank-box">
            🏦 <b>입금 계좌</b><br>
            은행 : <b>{BANK_NAME}</b><br>
            계좌번호 : <b>{BANK_ACCOUNT}</b><br>
            예금주 : <b>{BANK_HOLDER}</b>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.warning("관리자가 아직 입금 계좌를 설정하지 않았습니다.")

    st.markdown(
        """
        <div class="price-box">
        💎 <b>50크레딧</b> → <b>29,000원</b><br>
        💎 <b>100크레딧</b> → <b>57,000원</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

    package = st.selectbox(
        "충전할 크레딧",
        [
            "50크레딧 - 29,000원",
            "100크레딧 - 57,000원",
        ],
    )

    package_map = {
        "50크레딧 - 29,000원": (50, 29000),
        "100크레딧 - 57,000원": (100, 57000),
    }

    recharge_credits, recharge_amount = package_map[package]

    st.info(f"입금하실 금액은 **{recharge_amount:,}원**입니다.")

    depositor = st.text_input(
        "입금자명",
        placeholder="실제로 송금한 입금자명을 입력해주세요.",
    )

    if st.button(
        "✅ 입금 완료 · 충전 요청하기",
        type="primary",
        use_container_width=True,
    ):
        if not BANK_NAME or not BANK_ACCOUNT:
            st.error("입금 계좌가 설정되지 않았습니다.")

        elif not depositor.strip():
            st.error("입금자명을 입력해주세요.")

        else:
            ok, message = create_recharge_request(
                uid,
                package,
                recharge_credits,
                recharge_amount,
                depositor,
            )

            if ok:
                st.success(
                    "✅ 충전 요청이 접수되었습니다!\n\n"
                    "관리자가 입금을 확인한 뒤 크레딧을 지급해드립니다."
                )
                st.rerun()
            else:
                st.warning(message)

    st.divider()
    st.subheader("📋 내 충전 요청")

    # 이 함수는 이제 '충전' 메뉴를 눌렀을 때만 실행됨
    my_requests = get_my_recharge_requests(uid)

    if not my_requests:
        st.info("아직 충전 요청 내역이 없습니다.")
    else:
        for request in my_requests:
            package_name = request[0]
            req_amount = request[2]
            req_depositor = request[3]
            req_status = request[4]
            req_date = request[5]

            st.write(f"**{package_name}**")
            st.caption(
                f"입금자명: {req_depositor} · "
                f"{req_amount:,}원 · "
                f"상태: {req_status} · "
                f"{req_date.strftime('%Y-%m-%d %H:%M')}"
            )
            st.divider()


def render_students_page():
    st.subheader("👥 수강생 관리")

    # 이 함수는 관리자 '수강생 관리' 메뉴에서만 실행됨
    students = get_students()

    if not students:
        st.info("등록된 수강생이 없습니다.")
        return

    for student in students:
        student_id = student[0]
        student_username = student[1]
        student_name = student[2]
        student_credits = student[3]
        student_active = student[4]

        status_text = "이용중" if student_active else "정지"

        with st.expander(
            f"{student_name} · @{student_username} · "
            f"{student_credits}크레딧 · {status_text}"
        ):
            col1, col2, col3 = st.columns(3)

            add_credit = col1.number_input(
                "크레딧 지급",
                min_value=1,
                value=10,
                key=f"add_{student_id}",
            )

            if col1.button("지급", key=f"give_{student_id}"):
                change_credits(student_id, int(add_credit))
                st.rerun()

            minus_credit = col2.number_input(
                "크레딧 차감",
                min_value=1,
                value=10,
                key=f"minus_{student_id}",
            )

            if col2.button("차감", key=f"deduct_{student_id}"):
                change_credits(student_id, -int(minus_credit))
                st.rerun()

            active_button = "이용 정지" if student_active else "이용 재개"

            if col3.button(
                active_button,
                key=f"active_{student_id}",
            ):
                set_user_active(student_id, not student_active)
                st.rerun()


def render_recharge_admin_page():
    st.subheader("💳 충전 요청 관리")

    # 이 함수는 관리자 '충전 요청' 메뉴에서만 실행됨
    requests = get_pending_recharges()

    if not requests:
        st.info("현재 대기 중인 충전 요청이 없습니다.")
        return

    for req in requests:
        request_id = req[0]
        req_username = req[2]
        req_name = req[3]
        req_credits = req[5]
        req_amount = req[6]
        req_depositor = req[7]

        with st.container(border=True):
            st.write(f"**{req_name} (@{req_username})**")
            st.write(f"입금자명: **{req_depositor}**")
            st.write(f"금액: **{req_amount:,}원**")
            st.write(f"지급: **{req_credits}크레딧**")

            col1, col2 = st.columns(2)

            if col1.button(
                "✅ 입금 확인 · 승인",
                key=f"approve_{request_id}",
                use_container_width=True,
            ):
                ok, message = approve_recharge(request_id)

                if ok:
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)

            if col2.button(
                "❌ 거절",
                key=f"reject_{request_id}",
                use_container_width=True,
            ):
                reject_recharge(request_id)
                st.rerun()


def render_history_page():
    st.subheader("📋 최근 릴스 생성 기록")

    # 이 함수는 관리자 '사용 기록' 메뉴에서만 실행됨
    history = get_generation_history()

    if not history:
        st.info("아직 생성 기록이 없습니다.")
        return

    for item in history:
        created = item[0]
        history_username = item[1]
        history_name = item[2]
        product = item[3]
        history_type = item[4]
        history_duration = item[5]

        st.write(f"**{history_name} (@{history_username})**")
        st.write(f"{product} · {history_type} · {history_duration}")
        st.caption(created.strftime("%Y-%m-%d %H:%M"))
        st.divider()


# =========================================================
# 실제 메뉴
# =========================================================
if is_admin:
    st.success("👑 관리자 계정")

    admin_menu = st.radio(
        "관리자 메뉴",
        [
            "🎬 릴스 제작",
            "👥 수강생 관리",
            "💳 충전 요청",
            "📋 사용 기록",
        ],
        horizontal=True,
        label_visibility="collapsed",
        key="admin_menu",
    )

    st.divider()

    if admin_menu == "🎬 릴스 제작":
        render_create_page()
    elif admin_menu == "👥 수강생 관리":
        render_students_page()
    elif admin_menu == "💳 충전 요청":
        render_recharge_admin_page()
    elif admin_menu == "📋 사용 기록":
        render_history_page()

else:
    st.markdown(
        f"""
        <div class="credit-box">
        👤 <b>{name}</b>님
        &nbsp;&nbsp; | &nbsp;&nbsp;
        💎 현재 크레딧 <b>{credits}</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.caption("릴스 콘텐츠 1회 생성 시 1크레딧이 사용됩니다.")

    student_menu = st.radio(
        "수강생 메뉴",
        ["🎬 릴스 제작", "💳 크레딧 충전"],
        horizontal=True,
        label_visibility="collapsed",
        key="student_menu",
    )

    st.divider()

    if student_menu == "🎬 릴스 제작":
        render_create_page()
    elif student_menu == "💳 크레딧 충전":
        render_recharge_page()
