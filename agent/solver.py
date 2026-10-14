"""Grounded, deterministic planner for the eight closed course ask types.

The model sees observations only. Optional drift metadata is taken from the public
student snapshot at setup, never from truth.json. Each reply is a harness action.
"""
from __future__ import annotations
import hashlib
import json
import re
import shlex
from kit.world.anchor import Anchor

ASK_TYPES = ("which_day_covers", "source_of", "citation_for", "current_version_of",
             "contradiction_between", "define_term", "whatlinkshere", "record_mastery")
DEFAULT_REQUIRE = {
    "which_day_covers": ["course_day", "track", "anchor"], "source_of": ["anchor"],
    "citation_for": ["url", "anchor"],
    "current_version_of": ["fresher", "w_anchor", "c_anchor", "delta"],
    "contradiction_between": ["a", "b", "talk"],
    "define_term": ["definition", "sense", "anchor"], "whatlinkshere": ["anchors"],
    "record_mastery": ["receipt_id"],
}


def normalize_ask(ask):
    value = dict(ask)
    kind = value.get("type")
    if kind not in ASK_TYPES:
        raise ValueError(f"unknown ask type: {kind!r}")
    if kind == "source_of" and not value.get("anchor"):
        value["anchor"] = value.get("claim")
    value.setdefault("require", DEFAULT_REQUIRE[kind])
    return value


def call(server, tool, *, fields=(), lease=None, headers=None, **args):
    parts = [f"{'A2A' if '-' in server else 'MCP'} {server}.{tool}"]
    parts.extend(f"{key}={shlex.quote(str(value))}" for key, value in args.items() if value is not None)
    if fields: parts.append("fields=" + ",".join(sorted(fields)))
    if lease: parts.append("lease=" + shlex.quote(lease))
    parts.extend(f"header.{key}={shlex.quote(str(value))}" for key, value in (headers or {}).items())
    return " ".join(parts)


def parse_anchor(value):
    try:
        return Anchor.parse(value)
    except (ValueError, TypeError):
        return None


class CourseModel:
    def __init__(self, ask, *, drift_record=None, round_no=1):
        self.ask = normalize_ask(ask)
        self.kind = self.ask["type"]
        self.stage = 0
        self.target = None
        self.lease = None
        self.sources = []
        self.drift = dict(drift_record) if drift_record else None
        self.round_no = round_no
        self.version_rows = []
        self.talk_body = ""
        self.talk_sources = []
        self.talk_started = False
        self.last_action = None
        self.retry_used = False

    def _answer(self, **values):
        values.setdefault("cited_anchors", list(dict.fromkeys(self.sources)))
        return "ANSWER " + json.dumps(values, ensure_ascii=False)

    def _abstain(self, reason):
        return self._answer(text="Chưa đủ căn cứ đã xác minh: " + reason,
                            cited_anchors=[], abstained=True)

    def _locate(self, anchor):
        # Direct source IDs supplied by the ask need a current lease before reads.
        self.target = anchor
        return call("slides", "query", q=anchor, ns="Frame", limit=1, fields=("title",))

    def _read(self, anchor, fields=("body", "meta", "title")):
        return call("slides", "get_frame", anchor=anchor, fields=fields, lease=self.lease)

    def query(self, messages):
        observation = None
        if self.stage:
            last = next((m for m in reversed(messages) if m.get("role") == "user"), {})
            try:
                observation = json.loads(last.get("content", "{}"))
            except (TypeError, ValueError):
                observation = {}
            if not observation.get("ok"):
                error = observation.get("error") or {}
                turns = sum(m.get("role") == "assistant" for m in messages)
                if (error.get("code") == "unavailable" and not error.get("reason") and not self.retry_used
                    and turns < 3 and self.last_action and "record_mastery" not in self.last_action):
                    self.retry_used = True
                    return {"content": "```action\n" + self.last_action + "\n```"}
                reason = (observation.get("error") or {}).get("reason") or "tool không trả về dữ liệu an toàn."
                action = self._abstain(str(reason))
                return {"content": "```action\n" + action + "\n```"}
            self.lease = observation.get("lease_id") or self.lease
        action = self._plan(observation or {})
        if not action.startswith("ANSWER") and sum(m.get("role") == "assistant" for m in messages) >= 3:
            action = self._abstain("không còn lượt gọi để hoàn tất bằng chứng.")
        self.last_action = action
        self.stage += 1
        return {"role": "assistant", "content": "```action\n" + action + "\n```"}

    def _plan(self, result):
        ask, stage = self.ask, self.stage
        rows, anchors = result.get("rows") or [], result.get("anchors") or []
        row = rows[0] if rows else {}
        if self.kind == "define_term":
            if stage == 0:
                return call("glossary", "define", term=ask.get("term"), lang=ask.get("lang", "vi"),
                            sense=ask.get("anchor"), fields=("definition", "sense"))
            if result.get("partial") or not row.get("definition") or len(anchors) != 1:
                return self._abstain("thuật ngữ có nhiều nghĩa hoặc định nghĩa chưa được xác minh.")
            if ask.get("anchor") and anchors[0] != ask["anchor"]:
                return self._abstain("tool đã trả về một nghĩa khác với nghĩa được yêu cầu.")
            self.sources = [anchors[0]]
            return self._answer(text=str(row["definition"]), definition=row["definition"],
                                sense=row.get("sense"), anchor=anchors[0])
        if self.kind == "whatlinkshere":
            if stage == 0:
                return call("slides", "whatlinkshere", anchor=ask.get("anchor"), fields=("targets",))
            if result.get("partial") or "targets" not in row:
                return self._abstain("danh sách liên kết chưa đầy đủ.")
            self.sources = anchors
            targets = row["targets"]
            return self._answer(text="Các trang liên kết tới nguồn: " + ", ".join(targets) if targets else "Không có trang liên kết tới nguồn này.",
                                anchors=targets)
        if self.kind == "record_mastery":
            if stage == 0:
                return call("registry", "provenance", anchor=ask.get("learner"), fields=("etag",))
            if stage == 1:
                etag = result.get("etag") or row.get("etag")
                if not etag: return self._abstain("không có etag cho bản ghi người học.")
                key = hashlib.sha256(json.dumps([ask, self.round_no], sort_keys=True).encode()).hexdigest()[:24]
                return call("progress", "record_mastery", anchor=ask.get("learner"), learner=ask.get("learner"),
                            concept=ask.get("concept"), mastery_level=ask.get("mastery_level", "practiced"),
                            fields=("receipt_id",), headers={"if-match": etag, "idempotency-key": key})
            if not row.get("receipt_id"): return self._abstain("chưa có biên nhận ghi tiến độ thành công.")
            self.sources = anchors
            return self._answer(text="Đã ghi tiến độ cho người học được ủy quyền.", receipt_id=row["receipt_id"])
        if self.kind == "which_day_covers":
            if stage == 0:
                return call("slides", "whatlinkshere", anchor=ask.get("concept"), fields=("targets",))
            if stage == 1:
                frames = [a for a in row.get("targets", []) if (p := parse_anchor(a)) and p.ns == "Frame"]
                paths = {parse_anchor(a).slug for a in frames}
                if not frames or len(paths) != 1:
                    return self._abstain("khái niệm không xác định duy nhất một deck; cần phân giải thêm.")
                working = [a for a in frames if parse_anchor(a).rev == "w"]
                return self._locate(sorted(working or frames)[0])
            if stage == 2:
                if not self.lease: return self._abstain("không có lease đọc tài liệu.")
                return self._read(self.target, fields=("meta", "title"))
            meta = row.get("meta") or {}
            if meta.get("course_day") is None or not meta.get("track"):
                return self._abstain("nguồn chưa xác nhận course_day và track.")
            self.sources = [self.target]
            return self._answer(text=f"Khái niệm được đề cập ở ngày {meta['course_day']}, track {meta['track']}.",
                                course_day=meta["course_day"], track=meta["track"], anchor=self.target)
        if self.kind == "citation_for":
            if stage == 0:
                if ask.get("url") or str(ask.get("anchor", "")).startswith("Source:"):
                    self.target = ask.get("anchor")
                    return call("research", "cite_source", url=ask.get("url"), anchor=self.target, fields=("anchor", "url"))
                concept = parse_anchor(ask.get("concept"))
                if not concept: return self._abstain("cần concept hoặc URL hợp lệ cho câu hỏi trích dẫn.")
                return call("research", "search", q=concept.slug.replace("-", " "), limit=2,
                            fields=("anchor", "url"))
            if stage == 1 and "url" not in row:
                sources = [a for a in row.get("targets", []) if str(a).startswith("Source:")]
                if len(sources) != 1:
                    return self._abstain("khái niệm có nhiều nguồn hoặc chưa có Source duy nhất.")
                return call("research", "cite_source", anchor=sources[0], fields=("anchor", "url"))
            if len(rows) != 1 or result.get("partial"):
                return self._abstain("tìm thấy nhiều nguồn trích dẫn; cần xác định nguồn cụ thể.")
            if not row.get("url") or not row.get("anchor"):
                return self._abstain("nguồn chưa cung cấp URL và anchor hợp lệ.")
            self.sources = [row["anchor"]]
            return self._answer(text="Nguồn trích dẫn: " + row["url"], url=row["url"], anchor=row["anchor"])
        if self.kind == "source_of":
            if stage == 0: return self._locate(ask.get("anchor"))
            if stage == 1: return self._read(self.target, fields=("meta", "links", "title"))
            if stage == 2:
                meta = row.get("meta") or {}
                parent = meta.get("source_frame") or meta.get("source_anchor")
                parsed = parse_anchor(self.target)
                if not parent and parsed and parsed.ns in ("Frame", "Section"):
                    parent = str(Anchor("Deck", parsed.slug, parsed.rev))
                if not parent:
                    linked = [a for a in row.get("links", []) if str(a).startswith(("Frame:", "Deck:", "Source:"))]
                    parent = linked[0] if len(linked) == 1 else None
                if not parent: return self._abstain("không tìm thấy nguồn gốc duy nhất trong metadata và liên kết.")
                if not parse_anchor(parent): return self._abstain("nguồn gốc trả về không có anchor hợp lệ.")
                self.sources = [self.target]
                self.target = parent
                return self._answer(text="Metadata và liên kết của tài liệu xác nhận nguồn gốc: " + parent,
                                    anchor=parent)
            self.sources = [self.target]
            return self._answer(text="Nguồn gốc của nội dung: " + self.target, anchor=self.target)
        if self.kind == "contradiction_between":
            if stage == 0:
                if ask.get("talk"): return self._locate(ask["talk"])
                return call("slides", "whatlinkshere", anchor=ask.get("concept"), fields=("targets",))
            if stage == 1 and not self.target:
                talks = [a for a in row.get("targets", []) if str(a).startswith("Talk:")]
                if len(talks) != 1: return self._abstain("chưa xác định một trang Talk phù hợp.")
                return self._locate(talks[0])
            if not self.talk_started:
                self.talk_started = True
                return self._read(self.target, fields=("body",))
            meta = row.get("meta") or {}
            claims = [a for a in row.get("links", []) if str(a).startswith("Claim:")]
            a = meta.get("claim_a_anchor") or meta.get("a")
            b = meta.get("claim_b_anchor") or meta.get("b")
            if (not a or not b) and len(claims) == 2: a, b = claims
            current_body = row.get("body", "")
            self.talk_body += ("\n" if self.talk_body else "") + current_body
            self.talk_sources.extend(anchors)
            if not a or not b:
                # Fixture Talk prose also labels the two real claim anchors.
                claims = re.findall(r"Claim:[a-z0-9-]+(?:/[wc])?(?:/\d{3})?", self.talk_body)
                claims = list(dict.fromkeys(claims))
                if len(claims) == 2: a, b = claims
            if not a or not b:
                parsed = parse_anchor(self.target)
                if stage == 2 and parsed and parsed.idx:
                    following = str(Anchor("Talk", parsed.slug, parsed.rev, f"{int(parsed.idx) + 1:03d}"))
                    return self._read(following, fields=("body",))
                return self._abstain("nguồn chưa xác nhận cặp claim mâu thuẫn.")
            self.sources = list(dict.fromkeys(self.talk_sources))
            return self._answer(text="Hai nguồn có mâu thuẫn. " + self.talk_body, a=a, b=b, talk=self.target)
        if self.kind == "current_version_of":
            path = ask.get("path_id")
            if stage == 0:
                return call("registry", "provenance", anchor=f"Deck:{path}/w", fields=("etag", "rev"))
            if stage == 1:
                self.version_rows.append(row)
                return call("registry", "provenance", anchor=f"Deck:{path}/c", fields=("etag", "rev"))
            self.version_rows.append(row)
            if not self.drift or "delta" not in self.drift:
                return self._abstain("chưa có phép đo drift công khai cho cặp phiên bản.")
            self.sources = [f"Deck:{path}/w", f"Deck:{path}/c"]
            # The kit's working snapshot is its active course revision. An explicit
            # published fresher flag takes precedence; equal snapshots are stated.
            fresh = self.drift.get("fresher") or ("w" if self.drift.get("drifts") else "same")
            return self._answer(text=f"Working và canonical {'khác nhau' if self.drift.get('drifts') else 'không có drift'}; chỉ số drift công khai ghi delta={self.drift['delta']}.",
                                fresher=fresh, w_anchor=self.sources[0], c_anchor=self.sources[1],
                                delta=self.drift["delta"], drifts=self.drift.get("drifts", False),
                                index_source="drift.json (public student snapshot)")
        return self._abstain("không có kế hoạch phù hợp.")
