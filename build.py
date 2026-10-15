"""Package portable skill resources only; never include local account data."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parent
skill = root / "skill" / "mcd-fit-planner"
output = root / "dist" / "mcd-fit-planner-skill.zip"
output.parent.mkdir(exist_ok=True)
with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    paths = [skill / "SKILL.md"]
    paths += sorted((skill / "scripts").glob("*.py"))
    paths += sorted((skill / "references").glob("*.md"))
    paths += sorted((skill / "assets").glob("*.json"))
    for path in paths:
        archive.write(path, "mcd-fit-planner/" + path.relative_to(skill).as_posix())
print(output)

# WorkBuddy imports the contents of a skill ZIP; add its documented bilingual metadata.
workbuddy = root / "dist" / "mcd-fit-planner-workbuddy.zip"
source = (skill / "SKILL.md").read_text(encoding="utf-8")
_, frontmatter, body = source.split("---", 2)
extra = '''display_name: 麦当劳饮食计划助手
description_zh: 按循环饮食目标推荐麦当劳搭配，支持临时休息顺延、营养价差比较与月年消费账本。
description_en: Plan McDonald's meals around a saved training cycle and nutrition targets, compare substitutions, and track intake and spending.
version: 0.7.0
author: mcd-fit-planner contributors
'''
with ZipFile(workbuddy, "w", ZIP_DEFLATED) as archive:
    archive.writestr("SKILL.md", "---\n" + frontmatter.strip() + "\n" + extra + "---" + body)
    for path in paths:
        if path.name != "SKILL.md":
            archive.write(path, path.relative_to(skill).as_posix())
print(workbuddy)
