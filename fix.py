content = open("macro_tool.py", encoding="utf-8").read()
target = """    if any(step.get("action") == "browser_action" for step in steps) or any(
        step.get("action") == "ocr" and str(step.get("mode", "")).lower() == "browser" for step in steps
    ):
        lines.append("BrowserServerStarted := 0")
        lines.append("BrowserServerPort := 9233")
        lines.extend(browser_action_helpers())
        lines.append("")
    if any("""

replacement = """    if any(step.get("action") == "browser_action" for step in steps) or any(
        step.get("action") == "ocr" and str(step.get("mode", "")).lower() == "browser" for step in steps
    ):
        lines.append("BrowserServerStarted := 0")
        lines.append("BrowserServerPort := 9233")
        lines.extend(browser_action_helpers())
        lines.append("")
    has_ocr = any(step.get("action") == "ocr" for step in steps)
    if has_ocr:
        lines.append("OcrEngineStarted := 0")
        lines.append("OcrEnginePort := 9234")
        lines.extend(ocr_engine_helpers())
        lines.append("")
    if any("""

if target in content:
    content = content.replace(target, replacement)
    open("macro_tool.py", "w", encoding="utf-8").write(content)
    print("Replaced successfully")
else:
    print("Target not found")
