"""千川投放报表 CSV 导入器."""

import csv
from datetime import datetime
from pathlib import Path

from .column_maps import (
    AD_SPEND_COLUMN_MAPS,
    AD_SPEND_REQUIRED_FIELDS,
    detect_columns,
)


def import_ad_spend_csv(conn, file_path: str) -> dict:
    """导入千川投放报表 CSV 文件.

    Args:
        conn: sqlite3.Connection
        file_path: CSV 文件路径

    Returns:
        {"imported_count": int, "errors": [str], "mapping_used": dict, "unmatched_columns": [str]}
    """
    path = Path(file_path)
    if not path.exists():
        return {"imported_count": 0, "errors": [f"文件不存在: {file_path}"],
                "mapping_used": {}, "unmatched_columns": []}

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            rows = list(reader)
    except Exception as e:
        return {"imported_count": 0, "errors": [f"读取 CSV 失败: {e}"],
                "mapping_used": {}, "unmatched_columns": []}

    if not headers:
        return {"imported_count": 0, "errors": ["CSV 文件为空或无法解析表头"],
                "mapping_used": {}, "unmatched_columns": []}

    mapping, unmatched = detect_columns(headers, AD_SPEND_COLUMN_MAPS)

    # 检查必填字段
    missing_required = [f for f in AD_SPEND_REQUIRED_FIELDS if f not in mapping]
    if missing_required:
        return {
            "imported_count": 0,
            "errors": [f"缺少必填字段: {', '.join(missing_required)}"],
            "mapping_used": mapping,
            "unmatched_columns": unmatched,
        }

    now = datetime.now().isoformat()
    imported = 0
    errors: list[str] = []

    for i, row in enumerate(rows):
        try:
            values = {
                "campaign_name": str(row.get(mapping.get("campaign_name", ""), "") or "").strip(),
                "spend": float(row.get(mapping["spend"], 0) or 0),
                "impressions": int(float(row.get(mapping.get("impressions", ""), 0) or 0)),
                "clicks": int(float(row.get(mapping.get("clicks", ""), 0) or 0)),
                "conversions": int(float(row.get(mapping.get("conversions", ""), 0) or 0)),
                "date": str(row.get(mapping.get("date", ""), "") or "").strip(),
                "live_session_id": str(row.get(mapping.get("live_session_id", ""), "") or "").strip(),
                "imported_at": now,
            }

            conn.execute("""
                INSERT INTO ad_spend (campaign_name, spend, impressions,
                    clicks, conversions, date, live_session_id, imported_at)
                VALUES (:campaign_name, :spend, :impressions,
                    :clicks, :conversions, :date, :live_session_id, :imported_at)
            """, values)
            imported += 1
        except (ValueError, KeyError) as e:
            errors.append(f"第{i+2}行解析失败: {e}")

    conn.commit()

    return {
        "imported_count": imported,
        "errors": errors,
        "mapping_used": mapping,
        "unmatched_columns": unmatched,
    }
