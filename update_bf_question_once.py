import pymysql
import sys
import os
from datetime import date

sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# ==================== 修改这里的数据库连接信息 ====================
DB_CONFIG = {
    'host': os.getenv('BUFAN_DB_HOST', ''),
    'port': int(os.getenv('BUFAN_DB_PORT', '3306')),
    'user': os.getenv('BUFAN_DB_USER', ''),
    'password': os.getenv('BUFAN_DB_PASSWORD', ''),
    'database': os.getenv('BUFAN_DB_NAME', ''),
    'charset': 'utf8mb4'
}
# ================================================================

def main():
    missing = [key for key in ('host', 'user', 'password', 'database') if not DB_CONFIG.get(key)]
    if missing:
        print(f"⚠️ 未配置数据库连接信息（{', '.join(missing)}），跳过 bf_question 更新")
        return 0
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("数据库连接成功")

        # 每天轮换一个 user_id（7 天循环）
        USER_IDS = [10126, 10161, 10162, 10163, 10164, 10165, 10166]
        today_idx = date.today().weekday()  # 0=周一 ~ 6=周日
        user_id = USER_IDS[today_idx]
        print(f"今日分配 user_id = {user_id}（周{today_idx + 1}）")

        update_sql = f"""
        UPDATE bf_question 
        SET admin_id = 0, user_id = {user_id} 
        WHERE admin_id = 120 AND type = 1 AND deletetime IS NULL;
        """
        cursor.execute(update_sql)
        conn.commit()
        affected = cursor.rowcount
        print(f"✅ 更新完成，共影响 {affected} 条记录")

        cursor.close()
        conn.close()
    except pymysql.Error as e:
        print(f"数据库错误: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
