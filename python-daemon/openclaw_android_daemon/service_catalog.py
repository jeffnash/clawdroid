from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True, slots=True)
class ServiceCandidate:
    service: str
    aliases: tuple[str, ...]
    packages: tuple[str, ...]
    browser_domains: tuple[str, ...] = ()
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def browser_url(self) -> str | None:
        if not self.browser_domains:
            return None
        domain = self.browser_domains[0]
        if domain.startswith(("http://", "https://")):
            return domain
        return f"https://{domain}"

    def match_terms(self) -> tuple[tuple[str, str], ...]:
        terms: list[tuple[str, str]] = []
        if len(self.service) >= 2:
            terms.append(("service", self.service))
        terms.extend(("alias", alias) for alias in self.aliases)
        terms.extend(("domain", domain) for domain in self.browser_domains)
        terms.extend(("package", package) for package in self.packages)
        return tuple(terms)


@dataclass(frozen=True, slots=True)
class ServiceMatch:
    candidate: ServiceCandidate
    score: int
    matched_term: str
    matched_field: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.candidate.to_dict(),
            "match_score": self.score,
            "matched_term": self.matched_term,
            "matched_field": self.matched_field,
        }


SERVICE_CATALOG: tuple[ServiceCandidate, ...] = (
    ServiceCandidate(
        service="amazon",
        aliases=("amazon shopping", "amazon app", "amazon store"),
        packages=("com.amazon.mShop.android.shopping",),
        browser_domains=("amazon.com",),
        notes="Shopping and cart flows.",
    ),
    ServiceCandidate(
        service="prime_video",
        aliases=("prime video", "amazon prime video"),
        packages=("com.amazon.avod.thirdpartyclient",),
        browser_domains=("primevideo.com",),
        notes="Prime Video streaming flows.",
    ),
    ServiceCandidate(
        service="uber",
        aliases=("uber ride", "uber rides", "book an uber"),
        packages=("com.ubercab",),
        browser_domains=("uber.com",),
        notes="Ride-hailing flows.",
    ),
    ServiceCandidate(
        service="uber_eats",
        aliases=("uber eats", "ubereats"),
        packages=("com.ubercab.eats",),
        browser_domains=("ubereats.com",),
        notes="Food-ordering flows.",
    ),
    ServiceCandidate(
        service="doordash",
        aliases=("door dash", "doordash app"),
        packages=("com.dd.doordash",),
        browser_domains=("doordash.com",),
    ),
    ServiceCandidate(
        service="instacart",
        aliases=("instacart app",),
        packages=("com.instacart.client",),
        browser_domains=("instacart.com",),
    ),
    ServiceCandidate(
        service="airbnb",
        aliases=("air bnb", "airbnb app"),
        packages=("com.airbnb.android",),
        browser_domains=("airbnb.com",),
    ),
    ServiceCandidate(
        service="discord",
        aliases=("discord app", "discord server"),
        packages=("com.discord",),
        browser_domains=("discord.com",),
    ),
    ServiceCandidate(
        service="telegram",
        aliases=("telegram app", "telegram messenger", "tg"),
        packages=("org.telegram.messenger", "org.telegram.plus"),
        browser_domains=("telegram.org", "web.telegram.org"),
    ),
    ServiceCandidate(
        service="reddit",
        aliases=("reddit app",),
        packages=("com.reddit.frontpage",),
        browser_domains=("reddit.com",),
    ),
    ServiceCandidate(
        service="x",
        aliases=("twitter", "x app", "x twitter", "twitter x"),
        packages=("com.twitter.android",),
        browser_domains=("x.com", "twitter.com"),
    ),
    ServiceCandidate(
        service="instagram",
        aliases=("instagram app", "ig"),
        packages=("com.instagram.android",),
        browser_domains=("instagram.com",),
    ),
    ServiceCandidate(
        service="facebook",
        aliases=("facebook app", "meta facebook", "fb"),
        packages=("com.facebook.katana",),
        browser_domains=("facebook.com",),
    ),
    ServiceCandidate(
        service="tiktok",
        aliases=("tik tok", "tiktok app"),
        packages=("com.zhiliaoapp.musically",),
        browser_domains=("tiktok.com",),
    ),
    ServiceCandidate(
        service="netflix",
        aliases=("netflix app",),
        packages=("com.netflix.mediaclient",),
        browser_domains=("netflix.com",),
    ),
    ServiceCandidate(
        service="spotify",
        aliases=("spotify app",),
        packages=("com.spotify.music",),
        browser_domains=("spotify.com",),
    ),
)


def _normalize(value: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


def _score_term(needle: str, term: str) -> int:
    term_norm = _normalize(term)
    if not needle or not term_norm:
        return 0
    if term_norm == needle:
        return 1200 + min(len(term_norm), 60)
    if f" {term_norm} " in f" {needle} ":
        return 900 + min(len(term_norm), 60)
    if len(term_norm) >= 4 and term_norm in needle:
        return 720 + min(len(term_norm), 60)
    if len(needle) >= 4 and needle in term_norm:
        return 360 + min(len(needle), 60)
    needle_tokens = set(needle.split())
    term_tokens = set(term_norm.split())
    if term_tokens and needle_tokens:
        overlap = needle_tokens & term_tokens
        if overlap == term_tokens and len(overlap) >= 1:
            return 260 + 30 * len(overlap)
        if len(overlap) >= 2:
            return 180 + 25 * len(overlap)
    return 0


def resolve_services(query: str) -> list[ServiceMatch]:
    needle = _normalize(query or "")
    if not needle:
        return []
    matches: list[ServiceMatch] = []
    for candidate in SERVICE_CATALOG:
        best_score = 0
        best_term = ""
        best_field = ""
        for field_name, term in candidate.match_terms():
            score = _score_term(needle, term)
            if score > best_score:
                best_score = score
                best_term = term
                best_field = field_name
        if best_score <= 0:
            continue
        matches.append(
            ServiceMatch(
                candidate=candidate,
                score=best_score,
                matched_term=best_term,
                matched_field=best_field,
            )
        )
    matches.sort(key=lambda item: (item.score, item.candidate.service), reverse=True)
    return matches
