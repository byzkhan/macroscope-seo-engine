"""Tests for the QA engine."""

import pytest

from app.qa import (
    QACheck,
    QAResult,
    check_do_not_say,
    check_forbidden_claims,
    check_has_direct_answer,
    check_has_faq_section,
    check_heading_structure,
    check_internal_links,
    check_meta_description,
    check_slug,
    check_title_length,
    check_word_count,
    normalize_markdown_headings,
    run_qa,
    score_seo_aeo,
)


GOOD_ARTICLE = """\
# How AI Code Review Catches Bugs That Linters Miss

AI-powered code review catches logic errors, race conditions, and security vulnerabilities
that traditional linters fundamentally cannot detect. While linters verify syntax and enforce
formatting rules, AI review understands what your code is supposed to do and flags when it doesn't.

Every engineering team runs linters. They catch real issues and enforce consistency. But if your
quality strategy stops at linting, you're missing the bugs that actually cause production incidents.

## What Linters Actually Catch

Linters are pattern-matching tools. They excel at syntax errors, style enforcement, simple
anti-patterns, and import hygiene. Tools like ESLint, Pylint, and RuboCop handle these reliably.

But linters operate on structure, not meaning. They cannot determine if a function returns the
correct value for all inputs, or if error handling is complete.

## How AI Code Review Detects Logic Bugs

AI code review models analyze code semantically. They build understanding of what the PR is
changing, what the code should do, and where it falls short.

Consider this Python function:

```python
def calculate_discount(price, user):
    if user.is_premium:
        return price * 0.8
    if user.has_coupon:
        return price * 0.9
```

A linter sees valid Python. AI review catches the missing return for regular users without a coupon.

## Integrating AI Review Into Your Workflow

AI code review is a complement to linters, not a replacement. Add it as a PR check
alongside your existing tools. [Macroscope](https://macroscope.com) integrates with
GitHub and GitLab.

See our guide on [AI code review best practices](/blog/ai-code-review-best-practices)
for setup patterns.

Also check our analysis of [reducing PR cycle time](/blog/reducing-pr-cycle-time).

## Measuring the Impact

Track bugs caught per tool, production defect rates, and [engineering productivity
metrics](/blog/engineering-productivity-metrics) before and after adoption.

## Frequently Asked Questions

### What types of bugs does AI code review catch that linters miss?
AI code review catches logic errors, race conditions, incomplete error handling, and security
vulnerabilities that require understanding code intent rather than just syntax patterns.

### Does AI code review replace linters?
No. AI code review complements linters. Linters handle formatting and syntax efficiently.
AI review adds semantic analysis for logic bugs and architectural issues linters cannot detect.

### How accurate is AI code review compared to human reviewers?
AI catches certain categories like missing error handling more consistently than humans under
time pressure. Humans remain better at architectural judgment and business context evaluation.

### How does AI code review fit into a CI/CD pipeline?
AI review runs as a PR check alongside linters and tests. It triggers on PR creation, analyzes
the diff, and posts comments. Most teams start non-blocking, then promote to blocking after tuning.

### What is the ROI of adding AI code review?
Teams typically see reduced PR cycle time and fewer production defects within three to six months.
The return depends on team size, defect cost, and current review process maturity.

## Conclusion

Linters are necessary but not sufficient. AI code review fills the gap by analyzing semantically.

[Try Macroscope](https://macroscope.com) to see how AI code review fits into your workflow.
"""

BAD_ARTICLE = """\
Some text without any headings or structure.

This article has no FAQ section, no internal links, and poor formatting.
It is way too short to be useful for any purpose."""


class TestWordCount:
    def test_good_word_count(self):
        result = check_word_count(GOOD_ARTICLE, 300, 3000)
        assert result.passed is True

    def test_too_short(self):
        result = check_word_count("short", 800, 4000)
        assert result.passed is False
        assert "800" in result.message

    def test_too_long(self):
        long_text = "word " * 5000
        result = check_word_count(long_text, 800, 4000)
        assert result.passed is False

    def test_exact_boundaries(self):
        text = "word " * 800
        assert check_word_count(text, 800, 4000).passed is True


class TestFAQSection:
    def test_faq_present(self):
        assert check_has_faq_section(GOOD_ARTICLE).passed is True

    def test_faq_missing(self):
        assert check_has_faq_section(BAD_ARTICLE).passed is False

    def test_alternative_heading_common_questions(self):
        assert check_has_faq_section("## Common Questions\n\nContent.").passed is True

    def test_alternative_heading_frequently_asked(self):
        assert check_has_faq_section("### Frequently Asked Questions\n\nContent.").passed is True

    def test_setext_heading_is_recognized(self):
        content = "# Title\n\nFAQ\n---\n\nAnswer."
        assert check_has_faq_section(content).passed is True


class TestDirectAnswer:
    def test_direct_answer_present(self):
        assert check_has_direct_answer(GOOD_ARTICLE).passed is True

    def test_no_heading_no_answer(self):
        assert check_has_direct_answer(BAD_ARTICLE).passed is False

    def test_heading_but_thin_intro(self):
        thin = "# Title\n\nOne short sentence."
        assert check_has_direct_answer(thin).passed is False


class TestMetaDescription:
    def test_good_meta(self):
        meta = "Learn how AI code review catches bugs that linters miss in your pull requests."
        assert check_meta_description(meta).passed is True

    def test_too_short(self):
        assert check_meta_description("Too short").passed is False

    def test_too_long(self):
        assert check_meta_description("x" * 200).passed is False

    def test_boundary_50(self):
        assert check_meta_description("x" * 50).passed is True

    def test_boundary_160(self):
        assert check_meta_description("x" * 160).passed is True


class TestHeadingStructure:
    def test_valid_structure(self):
        assert check_heading_structure(GOOD_ARTICLE).passed is True

    def test_no_headings(self):
        result = check_heading_structure("Just plain text here.")
        assert result.passed is False

    def test_starts_with_h2(self):
        result = check_heading_structure("## Starting with H2\n\nContent.")
        assert result.passed is False
        assert "H2" in result.message

    def test_level_jump_h1_to_h4(self):
        content = "# Title\n\n#### Jumped to H4\n\nContent."
        result = check_heading_structure(content)
        assert result.passed is False

    def test_setext_headings_are_normalized(self):
        content = "# Title\n\nSection\n-------\n\nBody.\n"
        normalized = normalize_markdown_headings(content)
        assert "## Section" in normalized
        assert check_heading_structure(content).passed is True


class TestInternalLinks:
    def test_links_present(self):
        assert check_internal_links(GOOD_ARTICLE, min_links=2).passed is True

    def test_no_links(self):
        assert check_internal_links(BAD_ARTICLE, min_links=2).passed is False

    def test_macroscope_domain_links(self):
        content = "[a](https://macroscope.com/blog) and [b](https://macroscope.com/docs)"
        assert check_internal_links(content, min_links=2).passed is True

    def test_relative_path_links(self):
        content = "[a](/blog/post-1) and [b](/blog/post-2) and [c](/pricing)"
        assert check_internal_links(content, min_links=3).passed is True

    def test_external_links_dont_count(self):
        content = "[a](https://google.com) and [b](https://github.com)"
        assert check_internal_links(content, min_links=1).passed is False


class TestSlug:
    def test_valid_slug(self):
        assert check_slug("ai-code-review-bugs").passed is True

    def test_invalid_slug_spaces(self):
        assert check_slug("invalid slug").passed is False

    def test_invalid_slug_uppercase(self):
        assert check_slug("Invalid-Slug").passed is False

    def test_single_word_slug(self):
        assert check_slug("testing").passed is True


class TestForbiddenClaims:
    def test_clean_content(self):
        assert check_forbidden_claims("Clean content.", ["revolutionary"]).passed is True

    def test_forbidden_found(self):
        result = check_forbidden_claims("This revolutionary tool.", ["revolutionary"])
        assert result.passed is False
        assert "revolutionary" in result.message

    def test_case_insensitive(self):
        result = check_forbidden_claims("This REVOLUTIONARY tool.", ["revolutionary"])
        assert result.passed is False


class TestDoNotSay:
    def test_clean(self):
        assert check_do_not_say("Safe content.", ["avoid this"]).passed is True

    def test_found(self):
        assert check_do_not_say("We should avoid this.", ["avoid this"]).passed is False


class TestTitleLength:
    def test_good_length(self):
        assert check_title_length("How AI Code Review Catches Bugs").passed is True

    def test_too_long(self):
        assert check_title_length("x" * 80, max_len=60).passed is False


class TestSEOAEOScoring:
    def test_good_article_scores_well(self):
        score = score_seo_aeo(
            GOOD_ARTICLE,
            "Learn how AI code review catches bugs that linters miss in your pull requests.",
            "ai code review",
            "ai-code-review-bugs",
        )
        assert score.total > 50.0
        assert score.faq_presence_score > 0
        assert score.normalized > 0.5

    def test_bad_article_scores_poorly(self):
        score = score_seo_aeo(BAD_ARTICLE, "bad", "ai code review", "bad-article")
        assert score.total < 50.0
        assert score.faq_presence_score == 0.0

    def test_all_scores_within_bounds(self):
        score = score_seo_aeo(GOOD_ARTICLE, "meta desc", "keyword", "slug")
        for field_name in [
            "title_score", "meta_description_score", "keyword_density_score",
            "heading_structure_score", "internal_links_score", "faq_presence_score",
            "direct_answer_score", "readability_score", "content_depth_score",
            "freshness_signals_score",
        ]:
            val = getattr(score, field_name)
            assert 0.0 <= val <= 10.0, f"{field_name} out of bounds: {val}"


class TestRunQA:
    def test_good_article_passes(self):
        result = run_qa(
            content=GOOD_ARTICLE,
            slug="ai-code-review-bugs",
            meta_description="Learn how AI code review catches bugs that linters miss in PRs.",
            forbidden_claims=["revolutionary"],
            do_not_say=["game-changing"],
            min_word_count=300,
            max_word_count=3000,
            min_internal_links=2,
        )
        assert result.passed is True

    def test_bad_article_fails(self):
        result = run_qa(
            content=BAD_ARTICLE,
            slug="bad article",
            meta_description="bad",
            min_word_count=800,
        )
        assert result.passed is False
        assert len(result.failures) > 0

    def test_summary_format(self):
        result = run_qa(
            content=GOOD_ARTICLE,
            slug="valid-slug",
            meta_description="A good meta description that is the right length for SEO.",
            min_word_count=300,
            max_word_count=3000,
        )
        assert "/" in result.summary

    def test_to_dict(self):
        result = run_qa(content=GOOD_ARTICLE, slug="s", meta_description="x" * 60)
        d = result.to_dict()
        assert "passed" in d
        assert "checks" in d
        assert isinstance(d["checks"], list)
