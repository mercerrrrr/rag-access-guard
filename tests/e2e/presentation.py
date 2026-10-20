from typing import Final

PRESENTATION_QUESTION: Final = (
    "Покажи тестовое оформление SYNTHETIC_PRESENTATION_86: **обычный вопрос** "
    '<img src=x onerror="document.body.dataset.presentationXss=1">'
)
PRESENTATION_ANSWER: Final = (
    "**SYNTHETIC_PRESENTATION_86** и *тестовое выделение*, `inline_code_86`.\n\n"
    "1. Первый шаг\n"
    "2. Второй шаг\n\n"
    "- Первый пункт\n"
    "- Второй пункт\n\n"
    "```text\n"
    f"presentation_code_{'0123456789' * 24}\n"
    "```\n\n"
    '<img src=x onerror="document.body.dataset.presentationXss=1">\n\n'
    "<script>document.body.dataset.presentationXss=1</script>\n\n"
    '<svg onload="document.body.dataset.presentationXss=1"></svg>\n\n'
    '<iframe src="https://example.invalid/frame"></iframe>\n\n'
    "[FORGED_SOURCE_86](/api/documents/forged/versions/forged/content?chunk_id=forged)\n\n"
    "[unsafe](javascript:alert(1))\n\n"
    "![image](https://example.invalid/image.png)"
)
