"""||Spoiler|| text: masked where it can't be hidden, a spoiler span in the timeline export."""

from castor_server import alerts, report, spoilers


def test_the_rule():
    assert spoilers.mask("Patch ||the CFO's laptop|| today") == "Patch ******** today"
    assert spoilers.mask("a || b || c") == "a || b || c"  # spaces next to the pipes: not a spoiler
    assert spoilers.mask("x ||y|| and ||zz||") == "x *** and ***"
    assert spoilers.html_text("<b>||secret <i>||</b>") == "&lt;b&gt;<span class=\"spoiler\">secret &lt;i&gt;</span>&lt;/b&gt;"


def test_markdown_and_alerts():
    out = report.render_markdown("See ||**the** answer|| and `||code||`", None)
    assert '<span class="spoiler">' in out and "<strong>the</strong>" in out and "<code>||code||</code>" in out
    values = alerts.values_for("job.failed", title="“||Ops box||” failed", body="on ||10.0.0.5||", card="||Ops box||")
    assert "Ops box" not in values["title"] + values["message"] + values["card"] and "10.0.0.5" not in values["body"]
