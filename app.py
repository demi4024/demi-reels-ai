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
CREDIT_COST_PER_GENERATION = 1

st.set_page_config(
    page_title=APP_NAME,
    page_icon="🎬",
    layout="centered"
)


# =========================================================
# 디자인
# =========================================================

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

    h1 {
        font-size: 2rem !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# =========================================================
# Secrets 불러오기
# =========================================================

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


# =========================================================
# 비밀번호 암호화
# =========================================================

def hash_pw(password):
    return hashlib.sha256(
        password.encode("utf-8")
    ).hexdigest()


# =========================================================
# DB 연결
# =========================================================

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
        sslmode="require"
    )


# =========================================================
# DB 테이블 생성
# =========================================================

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

    conn.commit()
    cur.close()
    conn.close()


# =========================================================
# 관리자 생성
# =========================================================

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


# =========================================================
# 사용자 조회
# =========================================================

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


# =========================================================
# 회원가입
# =========================================================

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

        return (
            False,
            "이미 사용 중인 아이디입니다."
        )

    finally:

        cur.close()
        conn.close()


# =========================================================
# 크레딧 관리
# =========================================================

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


# =========================================================
# 생성 기록
# =========================================================

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


# =========================================================
# 충전 요청
# =========================================================

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

    return (
        True,
        "충전 요청이 접수되었습니다."
    )


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


# =========================================================
# 관리자 충전 요청 조회
# =========================================================

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


# =========================================================
# 충전 승인
# =========================================================

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

            return (
                False,
                "요청을 찾을 수 없습니다."
            )

        uid = request[0]
        credits = request[1]
        status = request[2]

        if status != "대기":

            conn.rollback()

            return (
                False,
                "이미 처리된 요청입니다."
            )

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

        return (
            True,
            f"{credits}크레딧 지급 완료!"
        )

    except Exception as e:

        conn.rollback()

        return (
            False,
            str(e)
        )

    finally:

        conn.close()


# =========================================================
# 충전 거절
# =========================================================

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


# =========================================================
# 관리자 수강생 목록
# =========================================================

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


# =========================================================
# 관리자 사용 기록
# =========================================================

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


# =========================================================
# AI 결과 분리
# =========================================================

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


# =========================================================
# DB 실행
# =========================================================

try:

    init_db()
    init_admin()

except Exception as e:

    st.error(
        "데이터베이스 연결 중 오류가 발생했습니다."
    )

    st.code(str(e))

    st.stop()


# =========================================================
# 로그인 세션
# =========================================================

if "user_id" not in st.session_state:
    st.session_state.user_id = None


def logout():

    st.session_state.user_id = None
    st.rerun()


# =========================================================
# 로그인 전
# =========================================================

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


# =========================================================
# 로그인 사용자 정보
# =========================================================

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


# =========================================================
# 상단
# =========================================================

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


# =========================================================
# 관리자 탭
# =========================================================

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


# =========================================================
# 수강생 탭
# =========================================================

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


# =========================================================
# 수강생 크레딧 충전
# =========================================================

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

        st.write("")

        package = st.selectbox(
            "충전할 크레딧",
            [
                "50크레딧 - 29,000원",
                "100크레딧 - 57,000원"
            ]
        )

        package_map = {

            "50크레딧 - 29,000원":
                (
                    50,
                    29000
                ),

            "100크레딧 - 57,000원":
                (
                    100,
                    57000
                )
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

        st.caption(
            "⚠️ 입금자명이 실제 송금자명과 다르면 "
            "확인이 늦어질 수 있습니다."
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
                req_credits = request[1]
                req_amount = request[2]
                req_depositor = request[3]
                req_status = request[4]
                req_date = request[5]

                if req_status == "대기":
                    status_icon = "⏳"

                elif req_status == "승인":
                    status_icon = "✅"

                else:
                    status_icon = "❌"

                st.write(
                    f"**{package_name}**"
                )

                st.write(
                    f"{status_icon} 상태 : "
                    f"**{req_status}**"
                )

                st.caption(
                    f"입금자명: {req_depositor} · "
                    f"{req_amount:,}원 · "
                    f"{req_date.strftime('%Y-%m-%d %H:%M')}"
                )

                st.divider()


# =========================================================
# 릴스 제작
# =========================================================

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
            "✍️ 추가 요청사항",
            placeholder=(
                "예: 댓글에 '정보' 남기게 해줘 / "
                "가격은 언급하지 마"
            )
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
                "크레딧이 부족합니다. "
                "크레딧 충전 메뉴에서 충전해주세요."
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
너는 인스타그램 릴스 전문 콘텐츠 기획자이자
숏폼 카피라이터야.

아래 정보를 바탕으로
실제로 촬영해서 사용할 수 있는
한국어 릴스 콘텐츠를 작성해줘.

[주제 또는 상품]
{topic}

[콘텐츠 유형]
{content_type}

[타깃]
{target if target else "일반 인스타그램 사용자"}

[영상 길이]
{duration}

[말투]
{tone}

[추가 요청]
{extra if extra else "없음"}

[작성 원칙]

1. 첫 1~3초 안에 스크롤을 멈출 수 있는
강한 후킹을 작성한다.

2. 서로 다른 방향의 후킹을 3개 제안한다.

3. 너무 광고 같은 표현은 피한다.

4. 실제 사람이 말하는 것처럼
자연스러운 한국어를 사용한다.

5. 한 문장은 짧게 작성한다.

6. 가능하면
문제 → 공감 → 궁금증 → 해결 → CTA
흐름을 사용한다.

7. 확인되지 않은 효능이나 기능을
임의로 만들어내지 않는다.

8. 제공되지 않은 가격, 할인율,
인증, 판매량 등을 만들어내지 않는다.

9. 영상 길이에 맞는 분량으로 작성한다.

10. 인스타 본문은 릴스 대본을
그대로 복사하지 않는다.

11. 인스타 본문은 읽기 쉽게 줄바꿈하고
자연스럽게 이모지를 사용한다.

12. 해시태그는 관련성 높은
한국어 해시태그 8~12개를 작성한다.

13. CTA는 댓글, 저장, 공유,
프로필 확인 중 콘텐츠에
가장 자연스러운 방식을 사용한다.

반드시 아래 형식으로 출력해.

[HOOKS]
1.
2.
3.

[SCRIPT]
릴스에서 실제로 말할 전체 대본

[SUBTITLES]
영상 화면에 넣을 자막을 한 줄씩 작성

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

                        client = OpenAI(
                            api_key=api_key
                        )

                        response = (
                            client.responses.create(
                                model="gpt-5-mini",
                                input=prompt
                            )
                        )

                        result = (
                            response.output_text
                        )


                    if not is_admin:

                        paid = deduct_credit(uid)

                        if not paid:

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


                    if not is_admin:

                        updated_user = get_user_by_id(uid)

                        st.info(
                            f"💎 남은 크레딧: "
                            f"{updated_user[4]}"
                        )


                    st.divider()

                    st.subheader(
                        "🔥 3초 후킹 3개"
                    )

                    st.code(
                        hooks,
                        language=None
                    )


                    st.subheader(
                        "🎬 릴스 대본"
                    )

                    st.code(
                        script,
                        language=None
                    )


                    st.subheader(
                        "📱 화면 자막"
                    )

                    st.code(
                        subtitles,
                        language=None
                    )


                    st.subheader(
                        "💬 CTA"
                    )

                    st.code(
                        cta,
                        language=None
                    )


                    st.subheader(
                        "✍️ 인스타 본문"
                    )

                    st.code(
                        caption,
                        language=None
                    )


                    st.subheader(
                        "#️⃣ 해시태그"
                    )

                    st.code(
                        hashtags,
                        language=None
                    )


                except Exception as e:

                    st.error(
                        "AI 생성 중 오류가 발생했습니다."
                    )

                    st.caption(
                        "생성에 실패한 경우 "
                        "크레딧은 차감되지 않습니다."
                    )

                    st.code(str(e))


# =========================================================
# 관리자 수강생 관리
# =========================================================

if is_admin:

    with students_tab:

        st.subheader(
            "👥 수강생 관리"
        )

        students = get_students()

        if not students:

            st.info(
                "가입한 수강생이 없습니다."
            )

        else:

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

                    add_credit = (
                        col1.number_input(
                            "크레딧 지급",
                            min_value=1,
                            value=10,
                            step=1,
                            key=f"add_{student_id}"
                        )
                    )

                    if col1.button(
                        "지급",
                        key=f"give_{student_id}"
                    ):

                        change_credits(
                            student_id,
                            int(add_credit)
                        )

                        st.success(
                            f"{add_credit}크레딧 지급 완료"
                        )

                        st.rerun()


                    minus_credit = (
                        col2.number_input(
                            "크레딧 차감",
                            min_value=1,
                            value=10,
                            step=1,
                            key=f"minus_{student_id}"
                        )
                    )

                    if col2.button(
                        "차감",
                        key=f"deduct_{student_id}"
                    ):

                        change_credits(
                            student_id,
                            -int(minus_credit)
                        )

                        st.success(
                            f"{minus_credit}크레딧 차감 완료"
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


# =========================================================
# 관리자 충전 요청
# =========================================================

if is_admin:

    with recharge_admin_tab:

        st.subheader(
            "💳 충전 요청 관리"
        )

        st.caption(
            "실제 계좌 입금을 확인한 뒤 "
            "승인 버튼을 눌러주세요."
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
                req_created = req[9]

                with st.container(
                    border=True
                ):

                    st.write(
                        f"### 👤 {req_name} "
                        f"(@{req_username})"
                    )

                    st.write(
                        f"🏦 입금자명 : "
                        f"**{req_depositor}**"
                    )

                    st.write(
                        f"💰 입금 확인 금액 : "
                        f"**{req_amount:,}원**"
                    )

                    st.write(
                        f"💎 지급 크레딧 : "
                        f"**{req_credits}크레딧**"
                    )

                    st.caption(
                        "요청 시간 : "
                        + req_created.strftime(
                            "%Y-%m-%d %H:%M"
                        )
                    )

                    approve_col, reject_col = (
                        st.columns(2)
                    )

                    if approve_col.button(
                        "✅ 입금 확인 · 승인",
                        key=f"approve_{request_id}",
                        type="primary",
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


                    if reject_col.button(
                        "❌ 요청 거절",
                        key=f"reject_{request_id}",
                        use_container_width=True
                    ):

                        reject_recharge(
                            request_id
                        )

                        st.warning(
                            "충전 요청을 거절했습니다."
                        )

                        st.rerun()


# =========================================================
# 관리자 사용 기록
# =========================================================

if is_admin:

    with history_tab:

        st.subheader(
            "📋 최근 릴스 생성 기록"
        )

        history = get_generation_history()

        if not history:

            st.info(
                "아직 생성 기록이 없습니다."
            )

        else:

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
