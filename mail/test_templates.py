from mail import templates
from mail.send import _html_shell


def test_email_code_template_renders_code_and_hosted_logo():
    subject, html = templates.render_email_code(code="482913")
    assert subject == "Your Midora verification code"
    assert "482913" in html
    assert "cid:" not in html
    assert templates.logo_url() in html


def test_notices_use_mjml_layout():
    html = _html_shell("Reset your password", "<p>Choose a new password</p>")
    assert "Reset your password" in html
    assert "<p>Choose a new password</p>" in html
    assert "$content" not in html and "$title" not in html


def test_every_template_compiles_without_cid_logo():
    for path in templates._TEMPLATES_DIR.glob("*.mjml"):
        html = templates._render(path.stem)
        assert "cid:midora_logo" not in html, path.name
