import streamlit as st
from openai import OpenAI

# -----------------------------
# 기본 설정
# -----------------------------
st.set_page_config(
    page_title="데미's 릴스 대본 제작기",
    page_icon="🎬",
    layout="centered"
)

st.title("🎬 데미's 릴스 대본 제작기")
st.caption("주제나 상품만 입력하면 릴스 대본부터 인스타 본문까지 한 번에 만들어드려요 ✨")

# -----------------------------
# API 연결
# -----------------------------
try:
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
except Exception:
    client = None

# -----------------------------
# 입력 영역
# -----------------------------
with st.form("reels_form"):

    topic = st.text_input(
        "📌 주제 / 상품명",
        placeholder="예: 자석식 메이크업 가방"
    )

    content_type = st.selectbox(
        "🎯 콘텐츠 유형",
        [
            "정보형",
            "공감형",
            "광고형",
            "CPA형",
            "공동구매형",
            "후기형",
            "조회수형"
        ]
    )

    target = st.text_input(
        "👤 타깃",
        placeholder="예: 20~30대 여성"
    )

    duration = st.selectbox(
        "⏱ 영상 길이",
        ["15초", "30초", "45초", "60초"]
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
        placeholder="예: 댓글에 '정보' 남기도록 유도해줘 / 가격은 언급하지 마"
    )

    submitted = st.form_submit_button(
        "✨ 릴스 콘텐츠 만들기",
        use_container_width=True
    )


# -----------------------------
# AI 생성
# -----------------------------
if submitted:

    if not topic:
        st.warning("주제나 상품명을 입력해주세요!")

    elif client is None:
        st.error("OPENAI_API_KEY가 아직 연결되지 않았어요.")

    else:

        prompt = f"""
너는 인스타그램 릴스 전문 콘텐츠 기획자이자 카피라이터야.

아래 정보를 바탕으로 실제 릴스에서 사용할 수 있는 콘텐츠를 만들어줘.

[입력 정보]
주제 또는 상품: {topic}
콘텐츠 유형: {content_type}
타깃: {target if target else "일반 인스타그램 사용자"}
영상 길이: {duration}
말투: {tone}
추가 요청: {extra if extra else "없음"}

[중요 작성 원칙]

1. 첫 3초 안에 시청자가 멈출 수 있도록 강한 후킹을 만든다.
2. 너무 광고처럼 느껴지는 표현은 피한다.
3. 실제 사람이 말하는 것처럼 자연스러운 한국어를 사용한다.
4. 한 문장은 짧게 만든다.
5. 영상 길이에 맞는 분량으로 작성한다.
6. 상품 콘텐츠인 경우 상품명만 반복하지 말고 문제 → 공감 → 해결 흐름을 활용한다.
7. 과장되거나 사실 확인이 필요한 효능은 임의로 만들어내지 않는다.
8. CTA는 저장, 댓글, 공유, 프로필 확인 등의 행동을 자연스럽게 유도한다.
9. 인스타 본문은 릴스 대본을 그대로 복사하지 말고 게시글용으로 새롭게 작성한다.
10. 해시태그는 관련성 높은 한국어 해시태그 위주로 8~12개 작성한다.

반드시 아래 형식을 정확하게 지켜서 출력해.

[HOOKS]
1. 후킹 문구
2. 후킹 문구
3. 후킹 문구

[SCRIPT]
릴스에서 실제로 말할 전체 대본

[SUBTITLES]
영상 화면에 짧게 넣을 자막을 한 줄씩 작성

[CTA]
영상 마지막에 사용할 CTA 문구 3개

[CAPTION]
인스타그램 게시물 본문.
보기 편하게 줄바꿈하고 이모지도 적절하게 사용.

[HASHTAGS]
해시태그만 한 줄로 작성
"""

        with st.spinner("릴스 콘텐츠를 만들고 있어요... ✨"):

            try:
                response = client.responses.create(
                    model="gpt-5.6-luna",
                    input=prompt
                )

                result = response.output_text

                # -----------------------------
                # 결과 분리
                # -----------------------------
                def get_section(text, start, end=None):
                    if start not in text:
                        return ""

                    content = text.split(start, 1)[1]

                    if end and end in content:
                        content = content.split(end, 1)[0]

                    return content.strip()


                hooks = get_section(result, "[HOOKS]", "[SCRIPT]")
                script = get_section(result, "[SCRIPT]", "[SUBTITLES]")
                subtitles = get_section(result, "[SUBTITLES]", "[CTA]")
                cta = get_section(result, "[CTA]", "[CAPTION]")
                caption = get_section(result, "[CAPTION]", "[HASHTAGS]")
                hashtags = get_section(result, "[HASHTAGS]")

                st.success("콘텐츠가 완성됐어요! 🎉")

                st.divider()

                st.subheader("🔥 3초 후킹")
                st.code(hooks, language=None)

                st.subheader("🎬 릴스 대본")
                st.code(script, language=None)

                st.subheader("📱 화면 자막")
                st.code(subtitles, language=None)

                st.subheader("💬 CTA")
                st.code(cta, language=None)

                st.subheader("✍️ 인스타 본문")
                st.code(caption, language=None)

                st.subheader("#️⃣ 해시태그")
                st.code(hashtags, language=None)

                st.info("각 결과 오른쪽의 복사 버튼을 눌러 바로 사용할 수 있어요.")

            except Exception as e:
                st.error("콘텐츠 생성 중 오류가 발생했어요.")
                st.write(e)
