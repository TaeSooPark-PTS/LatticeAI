import { registerCopy } from "./registry";
import type { NamespaceCopy } from "./types";

/**
 * Copy for the 연대기 (Chronicle) screen.
 *
 * The whole screen is a rearrangement of facts the Brain already stored, so the
 * words never claim more than that: no "분석", no "요약", no verb that would
 * suggest a model ran. Basic mode is the default reader here, so the vocabulary
 * stays at the level of a calendar — 자료 / 개념 / 연결 / 대화 — and every
 * machine token (`fact_superseded`, `web_url`) is spelled out as a sentence.
 */
export const chronicleCopy: NamespaceCopy = {
  ko: {
    "chronicle.kicker": "기억의 연대기",
    "chronicle.title": "두뇌가 자라온 시간",
    "chronicle.copy": "언제 무엇이 들어왔고 무엇이 달라졌는지, 날짜로 되짚어 봅니다.",
    "chronicle.loading": "연대기를 불러오는 중입니다.",
    "chronicle.empty.title": "오늘부터 기록이 쌓입니다",
    "chronicle.empty.detail": "자료를 넣거나 대화를 나누면, 그날부터 이 자리에 하루하루가 남습니다.",

    "chronicle.lane.sources": "자료",
    "chronicle.lane.entities": "개념",
    "chronicle.lane.connections": "연결",
    "chronicle.lane.conversations": "대화",

    "chronicle.growth.title": "쌓인 기억",
    "chronicle.growth.hint": "손잡이를 좌우로 옮기면 그 시점으로 돌아갑니다. 방향키로도 하루씩 움직일 수 있어요.",
    "chronicle.growth.aria": "시점 고르기",
    "chronicle.growth.valueText": "{date} · 자료 {sources} · 개념 {entities} · 연결 {connections} · 대화 {conversations}",
    "chronicle.growth.first": "처음",
    "chronicle.growth.latest": "지금",

    "chronicle.heatmap.title": "활동 달력",
    "chronicle.heatmap.hint": "진한 칸일수록 그날 많이 쌓였다는 뜻이에요. 칸을 누르면 그날 이야기가 아래에 열립니다.",
    "chronicle.heatmap.aria": "날짜별 활동",
    "chronicle.heatmap.cell": "{date}, {count}가지",
    "chronicle.heatmap.cellEmpty": "{date}, 쌓인 것 없음",
    "chronicle.heatmap.less": "적음",
    "chronicle.heatmap.more": "많음",
    "chronicle.weekday.sun": "일",
    "chronicle.weekday.mon": "월",
    "chronicle.weekday.tue": "화",
    "chronicle.weekday.wed": "수",
    "chronicle.weekday.thu": "목",
    "chronicle.weekday.fri": "금",
    "chronicle.weekday.sat": "토",

    "chronicle.day.title": "그날의 이야기",
    "chronicle.day.loading": "그날 이야기를 불러오는 중입니다.",
    "chronicle.day.quiet": "이 날은 조용했습니다. 남은 기록이 없어요.",
    "chronicle.group.sources": "자료",
    "chronicle.group.entities": "새로 생긴 개념",
    "chronicle.group.conversations": "나눈 대화",
    "chronicle.group.changes": "달라진 사실",
    "chronicle.group.count": "{count}개",
    "chronicle.group.empty": "이 날은 없어요.",
    "chronicle.group.more": "이 밖에 {count}개가 더 있어요.",

    "chronicle.open.source": "기억에서 찾아보기",
    "chronicle.open.entity": "지도에서 보기",
    "chronicle.open.conversation": "대화로 가기",
    "chronicle.open.change": "지도에서 보기",

    "chronicle.sourceType.upload": "올린 파일",
    "chronicle.sourceType.note": "메모",
    "chronicle.sourceType.web_url": "웹 페이지",
    "chronicle.sourceType.conversation": "대화",
    "chronicle.sourceType.local_file": "내 컴퓨터 파일",
    "chronicle.sourceType.image": "그림",
    "chronicle.sourceType.other": "자료",
    "chronicle.entityType.other": "개념",

    "chronicle.conversation.untitled": "제목 없는 대화",
    "chronicle.conversation.messages": "{count}번 주고받음",

    "chronicle.change.fact_superseded": "새 내용으로 바뀐 사실",
    "chronicle.change.fact_retired": "더 이상 사실이 아닌 것",
    "chronicle.change.connection_superseded": "다시 이어진 관계",
    "chronicle.change.connection_ended": "끊어진 관계",
    "chronicle.change.other": "달라진 것",

    "chronicle.rewind.title": "그때의 두뇌",
    "chronicle.rewind.subtitle": "{date}에 두뇌가 알고 있던 것",
    "chronicle.rewind.loading": "그때 모습을 불러오는 중입니다.",
    "chronicle.rewind.entities": "기억 조각",
    "chronicle.rewind.connections": "이어진 관계",
    "chronicle.rewind.note": "여기 두 숫자는 그때 저장돼 있던 모든 것을 셉니다. 위 곡선의 ‘개념’과는 세는 방식이 달라요.",
    "chronicle.rewind.top": "그때 중요했던 개념",
    "chronicle.rewind.topEmpty": "그때는 아직 눈에 띄는 개념이 없었어요.",
    "chronicle.rewind.reset": "지금으로 돌아오기",
  },
  en: {
    "chronicle.kicker": "Brain Chronicle",
    "chronicle.title": "How your Brain grew",
    "chronicle.copy": "Walk back through what arrived, and what changed, one day at a time.",
    "chronicle.loading": "Loading the chronicle.",
    "chronicle.empty.title": "The record starts today",
    "chronicle.empty.detail": "Add something or have a conversation, and that day will show up right here.",

    "chronicle.lane.sources": "Sources",
    "chronicle.lane.entities": "Ideas",
    "chronicle.lane.connections": "Links",
    "chronicle.lane.conversations": "Chats",

    "chronicle.growth.title": "What has piled up",
    "chronicle.growth.hint": "Drag the handle to travel back to that moment. Arrow keys move a day at a time.",
    "chronicle.growth.aria": "Pick a moment",
    "chronicle.growth.valueText": "{date} · {sources} sources · {entities} ideas · {connections} links · {conversations} chats",
    "chronicle.growth.first": "First",
    "chronicle.growth.latest": "Now",

    "chronicle.heatmap.title": "Activity calendar",
    "chronicle.heatmap.hint": "A darker square means more arrived that day. Pick one and its story opens below.",
    "chronicle.heatmap.aria": "Activity by day",
    "chronicle.heatmap.cell": "{date}, {count} things",
    "chronicle.heatmap.cellEmpty": "{date}, nothing arrived",
    "chronicle.heatmap.less": "Less",
    "chronicle.heatmap.more": "More",
    "chronicle.weekday.sun": "Sun",
    "chronicle.weekday.mon": "Mon",
    "chronicle.weekday.tue": "Tue",
    "chronicle.weekday.wed": "Wed",
    "chronicle.weekday.thu": "Thu",
    "chronicle.weekday.fri": "Fri",
    "chronicle.weekday.sat": "Sat",

    "chronicle.day.title": "That day's story",
    "chronicle.day.loading": "Loading that day.",
    "chronicle.day.quiet": "A quiet day — nothing was recorded.",
    "chronicle.group.sources": "Sources",
    "chronicle.group.entities": "New ideas",
    "chronicle.group.conversations": "Conversations",
    "chronicle.group.changes": "What changed",
    "chronicle.group.count": "{count}",
    "chronicle.group.empty": "Nothing this day.",
    "chronicle.group.more": "And {count} more.",

    "chronicle.open.source": "Find it in memory",
    "chronicle.open.entity": "See it on the map",
    "chronicle.open.conversation": "Go to conversations",
    "chronicle.open.change": "See it on the map",

    "chronicle.sourceType.upload": "Uploaded file",
    "chronicle.sourceType.note": "Note",
    "chronicle.sourceType.web_url": "Web page",
    "chronicle.sourceType.conversation": "Conversation",
    "chronicle.sourceType.local_file": "File on this computer",
    "chronicle.sourceType.image": "Picture",
    "chronicle.sourceType.other": "Source",
    "chronicle.entityType.other": "Idea",

    "chronicle.conversation.untitled": "Untitled conversation",
    "chronicle.conversation.messages": "{count} messages",

    "chronicle.change.fact_superseded": "Replaced by newer wording",
    "chronicle.change.fact_retired": "No longer true",
    "chronicle.change.connection_superseded": "Relinked a different way",
    "chronicle.change.connection_ended": "Link ended",
    "chronicle.change.other": "Changed",

    "chronicle.rewind.title": "Your Brain back then",
    "chronicle.rewind.subtitle": "What it knew on {date}",
    "chronicle.rewind.loading": "Loading how it looked then.",
    "chronicle.rewind.entities": "Pieces of memory",
    "chronicle.rewind.connections": "Links between them",
    "chronicle.rewind.note": "These two count everything stored at that moment. That is a different measure from the “ideas” line in the curve above.",
    "chronicle.rewind.top": "What mattered most then",
    "chronicle.rewind.topEmpty": "Nothing stood out yet at that point.",
    "chronicle.rewind.reset": "Back to now",
  },
};

// Route-scoped: registered when the Chronicle page's lazy chunk loads.
registerCopy(chronicleCopy);
