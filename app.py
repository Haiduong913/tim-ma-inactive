import datetime as dt
import socket
import xmlrpc.client
import pandas as pd
import streamlit as st

def _lan_ip():
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "localhost"

_LAN_LINK = f"http://{_lan_ip()}:8505"
_EXT_LINK = "https://inactive.tientho.com"   # ← đổi link Cloudflare tại đây

st.set_page_config(
    page_title="Tìm mã Inactive",
    page_icon="📦",
    layout="wide",
    menu_items={
        "About": f"""
### 📦 Tìm mã Inactive
Báo cáo phân tích & tìm mã sản phẩm cần Inactive trên hệ thống Odoo.

**🌐 Link nội bộ (LAN):** [{_LAN_LINK}]({_LAN_LINK})

**🔗 Link bên ngoài:** [{_EXT_LINK}]({_EXT_LINK})
""",
    },
)

URL      = "https://app.tientho.com"
DB       = "app_erp"
USERNAME = "TT01803"
PASSWORD = st.secrets.get("odoo_password", "hochoilientuc")

@st.cache_resource
def _uid():
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common")
    return common.authenticate(DB, USERNAME, PASSWORD, {})

def _models():
    return xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object")

def _search_read(model, domain, fields, limit=0, groupby=None):
    uid = _uid()
    models = _models()
    if groupby:
        return models.execute_kw(DB, uid, PASSWORD, model, "read_group",
                                 [domain, fields, groupby], {"lazy": False})
    return models.execute_kw(DB, uid, PASSWORD, model, "search_read",
                             [domain], {"fields": fields, "limit": limit})

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🗓 Kỳ báo cáo")
    c1, c2 = st.columns(2)
    bc_from = c1.date_input("Từ ngày", dt.date.today() - dt.timedelta(days=60), format="DD/MM/YYYY")
    bc_to   = c2.date_input("Đến ngày", dt.date.today(), format="DD/MM/YYYY")

    st.divider()
    st.markdown("### 🔎 Điều kiện lọc")
    f_khong_ban  = st.checkbox("Không phát sinh bán trong kỳ", value=True)
    f_khong_ton  = st.checkbox("Không phát sinh tồn trong kỳ", value=True)
    f_khong_nhap = st.checkbox("Không phát sinh nhập trong kỳ", value=True)
    f_khong_moi  = st.checkbox("Sản phẩm không tạo mới", value=True)
    n_ngay_moi   = 0
    if f_khong_moi:
        n_ngay_moi = st.number_input("Loại bỏ mã tạo trong N ngày gần nhất", min_value=1, max_value=3650, value=30, step=1)
    f_tat_ca     = st.checkbox("Tất cả sản phẩm (Active + Inactive)", value=False)

    st.divider()
    load = st.button("🚀 Tải dữ liệu", width="stretch", type="primary")

st.title("📦 Tìm mã Inactive")

if not st.session_state.get("_loaded"):
    st.info("**Bước 1:** Chọn kỳ ngày và điều kiện lọc.\n\n**Bước 2:** Bấm 🚀 **Tải dữ liệu**.")
    if load:
        st.session_state["_loaded"] = True
        st.rerun()
    st.stop()

if load:
    st.session_state.pop("_data", None)
    st.session_state["_loaded"] = True

_from_str = bc_from.isoformat()
_to_str   = bc_to.isoformat()

if "_data" not in st.session_state:
    with st.spinner("Đang truy vấn Odoo… (1–2 phút)"):

        # Toàn bộ mã — cả Active lẫn Inactive (lưu create_date để lọc mã mới)
        all_prods = _search_read("product.product",
                                  [["active", "in", [True, False]]],
                                  ["id", "active", "create_date"])
        all_ids      = set(p["id"] for p in all_prods)
        inactive_ids = set(p["id"] for p in all_prods if not p["active"])
        create_date  = {p["id"]: (p.get("create_date") or "")[:10] for p in all_prods}

        # Tồn kho
        quants = _search_read("stock.quant",
                               [["location_id.usage", "=", "internal"]],
                               ["product_id", "quantity"])
        stock = {}
        for q in quants:
            pid = q["product_id"][0]
            stock[pid] = stock.get(pid, 0) + q["quantity"]
        zero_stock = set(pid for pid in all_ids if round(stock.get(pid, 0), 4) == 0)

        # Doanh thu — Invoice (read_group để tránh timeout)
        inv_g = _search_read("account.move.line",
                              [["move_id.move_type", "=", "out_invoice"],
                               ["move_id.state", "=", "posted"],
                               ["move_id.invoice_date", ">=", _from_str],
                               ["move_id.invoice_date", "<=", _to_str],
                               ["product_id", "!=", False]],
                              ["product_id"], groupby=["product_id"])
        sold = set(g["product_id"][0] for g in inv_g if g.get("product_id"))

        # Doanh thu — POS (read_group)
        pos_g = _search_read("pos.order.line",
                              [["order_id.state", "in", ["done", "invoiced"]],
                               ["order_id.date_order", ">=", _from_str],
                               ["order_id.date_order", "<=", _to_str],
                               ["product_id", "!=", False]],
                              ["product_id"], groupby=["product_id"])
        sold |= set(g["product_id"][0] for g in pos_g if g.get("product_id"))

        # Nhập kho
        moves = _search_read("stock.move",
                              [["state", "=", "done"],
                               ["date", ">=", _from_str + " 00:00:00"],
                               ["date", "<=", _to_str   + " 23:59:59"],
                               ["location_dest_id.usage", "=", "internal"],
                               ["product_id", "!=", False]],
                              ["product_id"])
        imported = set(m["product_id"][0] for m in moves)

        st.session_state["_data"] = {
            "all_ids": all_ids,
            "inactive_ids": inactive_ids,
            "create_date": create_date,
            "stock": stock, "zero_stock": zero_stock,
            "sold": sold, "imported": imported,
        }

d = st.session_state["_data"]

# ── Áp điều kiện lọc ─────────────────────────────────────────────────────────
if f_tat_ca:
    candidate = set(d["all_ids"])
else:
    candidate = d["all_ids"] - d["inactive_ids"]

if f_khong_ban:   candidate -= d["sold"]
if f_khong_ton:   candidate &= d["zero_stock"]
if f_khong_nhap:  candidate -= d["imported"]
if f_khong_moi and n_ngay_moi > 0:
    cutoff = (bc_to - dt.timedelta(days=n_ngay_moi - 1)).isoformat()
    moi_trong_n = {pid for pid, cd in d["create_date"].items() if cd >= cutoff}
    candidate -= moi_trong_n

st.caption(f"Kỳ: **{bc_from:%d/%m/%Y} → {bc_to:%d/%m/%Y}** · Kết quả: **{len(candidate):,} mã**")

if not candidate:
    st.warning("Không có mã nào khớp điều kiện đã chọn.")
    st.stop()

# Lấy thông tin sản phẩm (batch 500)
with st.spinner(f"Đang lấy thông tin {len(candidate):,} sản phẩm…"):
    uid  = _uid()
    mdls = _models()
    pids = list(candidate)
    rows = []
    for i in range(0, len(pids), 500):
        chunk = pids[i:i+500]
        info = mdls.execute_kw(DB, uid, PASSWORD, "product.product", "search_read",
                               [[["id", "in", chunk], ["active", "in", [True, False]]]],
                               {"fields": ["id", "display_name", "barcode", "active", "create_date"]})
        for p in info:
            if not p.get("barcode"):
                continue
            pid = p["id"]
            rows.append({
                "Mã vạch":      p["barcode"],
                "Tên sản phẩm": p["display_name"],
                "Trạng thái":   "Inactive" if not p["active"] else "Active",
                "Tồn kho":      round(d["stock"].get(pid, 0), 2),
                "Có bán":   "✓" if pid in d["sold"]     else "",
                "Có nhập":  "✓" if pid in d["imported"] else "",
                "Ngày tạo": d["create_date"].get(pid, ""),
            })

df = pd.DataFrame(rows).sort_values(["Trạng thái", "Mã vạch"]).reset_index(drop=True)

st.dataframe(df, width="stretch", hide_index=True, height=600)

st.download_button("⬇️ Tải Excel",
                   df.to_csv(index=False).encode("utf-8-sig"),
                   file_name=f"tim_ma_inactive_{bc_from:%Y%m%d}_{bc_to:%Y%m%d}.csv",
                   mime="text/csv")
