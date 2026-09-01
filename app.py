import os
import hashlib
import secrets
from datetime import datetime, timedelta

import streamlit as st
import psycopg2
from psycopg2 import IntegrityError
from openai import OpenAI
from streamlit_cookies_controller import CookieController


APP_NAME = "데미's 릴스 대본 제작기"
DEFAULT_FREE_CREDITS = 30
CREDIT_COST_PER_GENERATION = 1

LOGIN_COOKIE_NAME = "demi_reels_login"
LOGIN_DAYS = 30


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
    unsafe_allow_html=True
)


def secret(name, default=""):
    try:
        value = st.secrets.get(name, "")
        if value:
            return str(value)
    except Exception:
        pass

    return os.getenv(name, default)


STUDENT_INVITE_CODE = secret(
    "DEMI_STUDENT_INVITE_CODE",
    ""
)

BANK_NAME = secret(
    "RECHARGE_BANK_NAME",
    ""
)

BANK_ACCOUNT = secret(
    "RECHARGE_ACCOUNT",
    ""
)

BANK_HOLDER = secret(
    "RECHARGE_ACCOUNT_HOLDER",
    ""
)


def hash_pw(password):
    return hashlib.sha256(
        password.encode("utf-8")
    ).hexdigest()


def hash_token(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def get_connection():

    db_url = secret(
        "SUPABASE_DB_URL",
        ""
    ).strip()

    if not db_url:
        raise RuntimeError(
            "SUPABASE_DB_URL이 설정되지 않았습니다."
        )

    return psycopg2.connect(
        db_url,
        sslmode="require",
        connect_timeout=10
    )


def init_db():

    conn = get_connection()
    cur = conn.cursor()

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

    conn.commit()
    cur.close()
    conn.close()


def init_admin():

    admin_id = secret(
        "DEMI_ADMIN_ID",
        "admin"
    ).strip()

    admin_password = secret(
        "DEMI_ADMIN_PASSWORD",
        ""
    ).strip()

    if not admin_password:
        return

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id
        FROM users
        WHERE username=%s
        """,
        (admin_id,)
    )

    row = cur.fetchone()

    if row:

        cur.execute(
            """
            UPDATE users
            SET
                password_hash=%s,
                name='관리자',
                is_active=TRUE,
                is_admin=TRUE
            WHERE username=%s
            """,
            (
                hash_pw(admin_password),
                admin_id
            )
        )

    else:

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
            VALUES
            (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                admin_id,
                hash_pw(admin_password),
                "관리자",
                999999,
                True,
                True,
                datetime.now()
            )
        )

    conn.commit()
    cur.close()
    conn.close()


def get_user(username):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
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
    conn.close()

    return row


def get_user_by_id(uid):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
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
    conn.close()

    return row


def create_user(username, password, name):

    conn = get_connection()
    cur = conn.cursor()

    try:

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
            VALUES
            (%s,%s,%s,%s,%s,%s,%s)
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

        conn.commit()

        return True, None

    except IntegrityError:

        conn.rollback()

        return False, "이미 사용 중인 아이디입니다."

    finally:

        cur.close()
        conn.close()


def change_credits(uid, amount):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET credits = GREATEST(0, credits + %s)
        WHERE id=%s
        """,
        (
            amount,
            uid
        )
    )

    conn.commit()

    cur.close()
    conn.close()


def deduct_credit(uid):

    conn = get_connection()
    cur = conn.cursor()

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
            CREDIT_COST_PER_GENERATION
        )
    )

    row = cur.fetchone()

    if row:
        conn.commit()
    else:
        conn.rollback()

    cur.close()
    conn.close()

    return bool(row)


def create_login_token(uid):

    token = secrets.token_urlsafe(32)
    token_hash = hash_token(token)

    expires_at = (
        datetime.now()
        + timedelta(days=LOGIN_DAYS)
    )

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        DELETE FROM login_tokens
        WHERE user_id=%s
           OR expires_at < %s
        """,
        (
            uid,
            datetime.now()
        )
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
        VALUES
        (%s,%s,%s,%s)
        """,
        (
            uid,
            token_hash,
            expires_at,
            datetime.now()
        )
    )

    conn.commit()

    cur.close()
    conn.close()

    return token


def get_user_from_token(token):

    if not token:
        return None

    token_hash = hash_token(token)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
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
        (
            token_hash,
            datetime.now()
        )
    )

    row = cur.fetchone()

    cur.close()
    conn.close()

    return row


def delete_login_token(token):

    if not token:
        return

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        DELETE FROM login_tokens
        WHERE token_hash=%s
        """,
        (
            hash_token(token),
        )
    )

    conn.commit()

    cur.close()
    conn.close()


def save_generation(
    uid,
    product_name,
    content_type,
    duration,
    target,
    tone,
    result
):

    conn = get_connection()
    cur = conn.cursor()

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
        VALUES
        (%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
            datetime.now()
        )
    )

    conn.commit()

    cur.close()
    conn.close()


def create_recharge_request(
    uid,
    package_name,
    credits,
    amount,
    depositor
):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id
        FROM reels_recharge_requests
        WHERE user_id=%s
          AND status='대기'
        """,
        (uid,)
    )

    pending = cur.fetchone()

    if pending:

        cur.close()
        conn.close()

        return (
            False,
            "이미 처리 대기 중인 충전 요청이 있습니다."
        )

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
        VALUES
        (%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            uid,
            package_name,
            credits,
            amount,
            depositor.strip(),
            "대기",
            datetime.now()
        )
    )

    conn.commit()

    cur.close()
    conn.close()

    return True, "충전 요청이 접수되었습니다."


def get_my_recharge_requests(uid):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
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
        (uid,)
    )

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows


def get_pending_recharges():

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
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

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows


def approve_recharge(request_id):

    conn = get_connection()

    try:

        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                user_id,
                credits,
                status
            FROM reels_recharge_requests
            WHERE id=%s
            FOR UPDATE
            """,
            (request_id,)
        )

        request = cur.fetchone()

        if not request:
            conn.rollback()
            return False, "요청을 찾을 수 없습니다."

        uid = request[0]
        credits = request[1]
        status = request[2]

        if status != "대기":
            conn.rollback()
            return False, "이미 처리된 요청입니다."

        cur.execute(
            """
            UPDATE users
            SET credits = credits + %s
            WHERE id=%s
            """,
            (
                credits,
                uid
            )
        )

        cur.execute(
            """
            UPDATE reels_recharge_requests
            SET
                status='승인',
                processed_at=%s
            WHERE id=%s
            """,
            (
                datetime.now(),
                request_id
            )
        )

        conn.commit()

        return True, f"{credits}크레딧 지급 완료!"

    except Exception as e:

        conn.rollback()

        return False, str(e)

    finally:

        conn.close()


def reject_recharge(request_id):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE reels_recharge_requests
        SET
            status='거절',
            processed_at=%s
        WHERE id=%s
          AND status='대기'
        """,
        (
            datetime.now(),
            request_id
        )
    )

    conn.commit()

    cur.close()
    conn.close()


def get_students():

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
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

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows


def set_user_active(uid, active):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET is_active=%s
        WHERE id=%s
        """,
        (
            active,
            uid
        )
    )

    conn.commit()

    cur.close()
    conn.close()


def get_generation_history():

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
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

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows


def get_section(text, start, end=None):

    if start not in text:
        return ""

    content = text.split(
        start,
        1
    )[1]

    if end and end in content:

        content = content.split(
            end,
            1
        )[0]

    return content.strip()


try:
    init_db()
    init_admin()

except Exception as e:

    st.error(
        "데이터베이스 연결 중 오류가 발생했습니다."
    )

    st.code(str(e))

    st.stop()


controller = CookieController()


if "user_id" not in st.session_state:
    st.session_state.user_id = None


if not st.session_state.user_id:

    try:

        saved_token = controller.get(
            LOGIN_COOKIE_NAME
        )

        if saved_token:

            saved_user = get_user_from_token(
                saved_token
            )

            if (
                saved_user
                and saved_user[5]
            ):

                st.session_state.user_id = (
                    saved_user[0]
                )

    except Exception:
        pass


def logout():

    try:

        token = controller.get(
            LOGIN_COOKIE_NAME
        )

        if token:
            delete_login_token(token)

        controller.remove(
            LOGIN_COOKIE_NAME
        )

    except Exception:
        pass

    st.session_state.user_id = None
    st.rerun()


if not st.session_state.user_id:

    st.title(
        "🎬 데미's 릴스 대본 제작기"
    )

    st.caption(
        "주제나 상품만 입력하면 "
        "후킹부터 릴스 대본, "
        "인스타 본문까지 한 번에 ✨"
    )

    login_tab, signup_tab = st.tabs(
        [
            "🔐 로그인",
            "✨ 수강생 가입"
        ]
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

            user = get_user(
                login_id.strip()
            )

            if (
                not user
                or user[2] != hash_pw(login_pw)
            ):

                st.error(
                    "아이디 또는 비밀번호를 확인해주세요."
                )

            elif not user[5]:

                st.error(
                    "현재 이용이 정지된 계정입니다."
                )

            else:

                login_token = create_login_token(
                    user[0]
                )

                controller.set(
                    LOGIN_COOKIE_NAME,
                    login_token,
                    max_age=LOGIN_DAYS * 24 * 60 * 60
                )

                st.session_state.user_id = user[0]

                st.rerun()


    with signup_tab:

        signup_name = st.text_input(
            "이름",
            key="signup_name"
        )

        signup_id = st.text_input(
            "아이디",
            key="signup_id"
        )

        signup_pw1 = st.text_input(
            "비밀번호",
            type="password",
            key="signup_pw1"
        )

        signup_pw2 = st.text_input(
            "비밀번호 확인",
            type="password",
            key="signup_pw2"
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

            if not signup_name.strip():

                st.error(
                    "이름을 입력해주세요."
                )

            elif not signup_id.strip():

                st.error(
                    "아이디를 입력해주세요."
                )

            elif len(signup_pw1) < 4:

                st.error(
                    "비밀번호는 4자리 이상 입력해주세요."
                )

            elif signup_pw1 != signup_pw2:

                st.error(
                    "비밀번호가 서로 다릅니다."
                )

            elif (
                STUDENT_INVITE_CODE
                and invite_code
                != STUDENT_INVITE_CODE
            ):

                st.error(
                    "수강생 초대코드가 올바르지 않습니다."
                )

            else:

                ok, msg = create_user(
                    signup_id,
                    signup_pw1,
                    signup_name
                )

                if ok:

                    st.success(
                        f"가입 완료! "
                        f"{DEFAULT_FREE_CREDITS}크레딧이 지급되었습니다."
                    )

                else:

                    st.error(msg)

    st.stop()


user = get_user_by_id(
    st.session_state.user_id
)

if not user:
    logout()


uid = user[0]
username = user[1]
name = user[3]
credits = user[4]
is_active = user[5]
is_admin = user[6]


if (
    not is_active
    and not is_admin
):

    st.error(
        "현재 이용이 정지된 계정입니다."
    )

    if st.button("로그아웃"):
        logout()

    st.stop()


top1, top2 = st.columns(
    [5, 1]
)

with top1:

    st.title(
        "🎬 데미's 릴스 대본 제작기"
    )

with top2:

    if st.button(
        "로그아웃"
    ):
        logout()


if is_admin:

    st.success(
        "👑 관리자 계정"
    )

    (
        create_tab,
        students_tab,
        recharge_admin_tab,
        history_tab
    ) = st.tabs(
        [
            "🎬 릴스 제작",
            "👥 수강생 관리",
            "💳 충전 요청",
            "📋 사용 기록"
        ]
    )


else:

    st.markdown(
        f"""
        <div class="credit-box">
        👤 <b>{name}</b>님
        &nbsp;&nbsp; | &nbsp;&nbsp;
        💎 현재 크레딧
        <b>{credits}</b>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.caption(
        "릴스 콘텐츠 1회 생성 시 "
        "1크레딧이 사용됩니다."
    )

    (
        create_tab,
        recharge_tab
    ) = st.tabs(
        [
            "🎬 릴스 제작",
            "💳 크레딧 충전"
        ]
    )


if not is_admin:

    with recharge_tab:

        st.subheader(
            "💳 크레딧 충전"
        )

        st.write(
            "아래 계좌로 먼저 입금한 뒤 "
            "충전 요청을 보내주세요."
        )

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
                unsafe_allow_html=True
            )

        else:

            st.warning(
                "관리자가 아직 입금 계좌를 설정하지 않았습니다."
            )


        st.markdown(
            """
            <div class="price-box">
            💎 <b>50크레딧</b> → <b>29,000원</b><br>
            💎 <b>100크레딧</b> → <b>57,000원</b>
            </div>
            """,
            unsafe_allow_html=True
        )

        package = st.selectbox(
            "충전할 크레딧",
            [
                "50크레딧 - 29,000원",
                "100크레딧 - 57,000원"
            ]
        )

        package_map = {

            "50크레딧 - 29,000원":
                (50, 29000),

            "100크레딧 - 57,000원":
                (100, 57000)
        }

        recharge_credits, recharge_amount = (
            package_map[package]
        )

        st.info(
            f"입금하실 금액은 "
            f"**{recharge_amount:,}원**입니다."
        )

        depositor = st.text_input(
            "입금자명",
            placeholder="실제로 송금한 입금자명을 입력해주세요."
        )

        if st.button(
            "✅ 입금 완료 · 충전 요청하기",
            type="primary",
            use_container_width=True
        ):

            if not BANK_NAME or not BANK_ACCOUNT:

                st.error(
                    "입금 계좌가 설정되지 않았습니다."
                )

            elif not depositor.strip():

                st.error(
                    "입금자명을 입력해주세요."
                )

            else:

                ok, message = (
                    create_recharge_request(
                        uid,
                        package,
                        recharge_credits,
                        recharge_amount,
                        depositor
                    )
                )

                if ok:

                    st.success(
                        "✅ 충전 요청이 접수되었습니다!\n\n"
                        "관리자가 입금을 확인한 뒤 "
                        "크레딧을 지급해드립니다."
                    )

                else:

                    st.warning(message)


        st.divider()

        st.subheader(
            "📋 내 충전 요청"
        )

        my_requests = (
            get_my_recharge_requests(uid)
        )

        if not my_requests:

            st.info(
                "아직 충전 요청 내역이 없습니다."
            )

        else:

            for request in my_requests:

                package_name = request[0]
                req_amount = request[2]
                req_depositor = request[3]
                req_status = request[4]
                req_date = request[5]

                st.write(
                    f"**{package_name}**"
                )

                st.caption(
                    f"입금자명: {req_depositor} · "
                    f"{req_amount:,}원 · "
                    f"상태: {req_status} · "
                    f"{req_date.strftime('%Y-%m-%d %H:%M')}"
                )

                st.divider()


with create_tab:

    st.subheader(
        "✨ 릴스 콘텐츠 만들기"
    )

    with st.form(
        "reels_form"
    ):

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
            "✍️ 추가 요청사항"
        )

        submitted = (
            st.form_submit_button(
                "✨ 릴스 콘텐츠 만들기",
                type="primary",
                use_container_width=True
            )
        )


    if submitted:

        latest_user = get_user_by_id(uid)
        latest_credits = latest_user[4]

        if not topic.strip():

            st.warning(
                "주제나 상품명을 입력해주세요."
            )

        elif (
            not is_admin
            and latest_credits
            < CREDIT_COST_PER_GENERATION
        ):

            st.error(
                "크레딧이 부족합니다."
            )

        else:

            api_key = secret(
                "OPENAI_API_KEY",
                ""
            ).strip()

            if not api_key:

                st.error(
                    "OPENAI_API_KEY가 설정되지 않았습니다."
                )

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

                    with st.spinner(
                        "릴스 콘텐츠를 만들고 있어요... ✨"
                    ):

                        client = OpenAI(
                            api_key=api_key
                        )

                        response = client.responses.create(
                            model="gpt-5-mini",
                            input=prompt
                        )

                        result = response.output_text


                    if not is_admin:

                        if not deduct_credit(uid):

                            st.error(
                                "크레딧이 부족합니다."
                            )

                            st.stop()


                    save_generation(
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


                    st.success(
                        "콘텐츠가 완성됐어요! 🎉"
                    )

                    st.subheader(
                        "🔥 3초 후킹 3개"
                    )
                    st.code(hooks)

                    st.subheader(
                        "🎬 릴스 대본"
                    )
                    st.code(script)

                    st.subheader(
                        "📱 화면 자막"
                    )
                    st.code(subtitles)

                    st.subheader(
                        "💬 CTA"
                    )
                    st.code(cta)

                    st.subheader(
                        "✍️ 인스타 본문"
                    )
                    st.code(caption)

                    st.subheader(
                        "#️⃣ 해시태그"
                    )
                    st.code(hashtags)


                except Exception as e:

                    st.error(
                        "AI 생성 중 오류가 발생했습니다."
                    )

                    st.code(str(e))


if is_admin:

    with students_tab:

        st.subheader(
            "👥 수강생 관리"
        )

        students = get_students()

        for student in students:

            student_id = student[0]
            student_username = student[1]
            student_name = student[2]
            student_credits = student[3]
            student_active = student[4]

            status_text = (
                "이용중"
                if student_active
                else "정지"
            )

            with st.expander(
                f"{student_name} · "
                f"@{student_username} · "
                f"{student_credits}크레딧 · "
                f"{status_text}"
            ):

                col1, col2, col3 = (
                    st.columns(3)
                )

                add_credit = col1.number_input(
                    "크레딧 지급",
                    min_value=1,
                    value=10,
                    key=f"add_{student_id}"
                )

                if col1.button(
                    "지급",
                    key=f"give_{student_id}"
                ):

                    change_credits(
                        student_id,
                        int(add_credit)
                    )

                    st.rerun()


                minus_credit = col2.number_input(
                    "크레딧 차감",
                    min_value=1,
                    value=10,
                    key=f"minus_{student_id}"
                )

                if col2.button(
                    "차감",
                    key=f"deduct_{student_id}"
                ):

                    change_credits(
                        student_id,
                        -int(minus_credit)
                    )

                    st.rerun()


                active_button = (
                    "이용 정지"
                    if student_active
                    else "이용 재개"
                )

                if col3.button(
                    active_button,
                    key=f"active_{student_id}"
                ):

                    set_user_active(
                        student_id,
                        not student_active
                    )

                    st.rerun()


    with recharge_admin_tab:

        st.subheader(
            "💳 충전 요청 관리"
        )

        requests = get_pending_recharges()

        if not requests:

            st.info(
                "현재 대기 중인 충전 요청이 없습니다."
            )

        else:

            for req in requests:

                request_id = req[0]
                req_username = req[2]
                req_name = req[3]
                req_credits = req[5]
                req_amount = req[6]
                req_depositor = req[7]

                with st.container(
                    border=True
                ):

                    st.write(
                        f"**{req_name} "
                        f"(@{req_username})**"
                    )

                    st.write(
                        f"입금자명: "
                        f"**{req_depositor}**"
                    )

                    st.write(
                        f"금액: "
                        f"**{req_amount:,}원**"
                    )

                    st.write(
                        f"지급: "
                        f"**{req_credits}크레딧**"
                    )

                    col1, col2 = st.columns(2)

                    if col1.button(
                        "✅ 입금 확인 · 승인",
                        key=f"approve_{request_id}",
                        use_container_width=True
                    ):

                        ok, message = (
                            approve_recharge(
                                request_id
                            )
                        )

                        if ok:
                            st.success(message)
                            st.rerun()
                        else:
                            st.error(message)


                    if col2.button(
                        "❌ 거절",
                        key=f"reject_{request_id}",
                        use_container_width=True
                    ):

                        reject_recharge(
                            request_id
                        )

                        st.rerun()


    with history_tab:

        st.subheader(
            "📋 최근 릴스 생성 기록"
        )

        history = get_generation_history()

        for item in history:

            created = item[0]
            history_username = item[1]
            history_name = item[2]
            product = item[3]
            history_type = item[4]
            history_duration = item[5]

            st.write(
                f"**{history_name} "
                f"(@{history_username})**"
            )

            st.write(
                f"{product} · "
                f"{history_type} · "
                f"{history_duration}"
            )

            st.caption(
                created.strftime(
                    "%Y-%m-%d %H:%M"
                )
            )

            st.divider()
