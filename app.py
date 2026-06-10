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
_EXT_LINK = "https://inactive.tientho.com"

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
PASSWORD = st.secrets["odoo_password"]

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
    st.markdown("### 🗓 Đến ngày")
    bc_to = st.date_input("", dt.date.today(), format="DD/MM/YYYY", label_visibility="collapsed")

    st.divider()
    st.markdown("### 🔎 Điều kiện lọc")

    f_khong_ban = st.checkbox("Không phát sinh bán trong", value=True)
    n_ban = 0
    if f_khong_ban:
        n_ban = int(st.number_input("ngày (bán)", min_value=1, max_value=3650, value=60, step=1, label_visibility="collapsed", key="n_ban"))

    f_khong_ton = st.checkbox("Không phát sinh tồn kho", value=True)

    f_khong_nhap = st.checkbox("Không phát sinh nhập trong", value=True)
    n_nhap = 0
    if f_khong_nhap:
        n_nhap = int(st.number_input("ngày (nhập)", min_value=1, max_value=3650, value=60, step=1, label_visibility="collapsed", key="n_nhap"))

    f_khong_moi = st.checkbox("Sản phẩm không tạo mới trong", value=True)
    n_moi = 0
    if f_khong_moi:
        n_moi = int(st.number_input("ngày (tạo mới)", min_value=1, max_value=3650, value=30, step=1, label_visibility="collapsed", key="n_moi"))

    f_tat_ca = st.checkbox("Tất cả sản phẩm (Active + Inactive)", value=False)

    st.divider()
    load = st.button("🚀 Tải dữ liệu", width="stretch", type="primary")

st.title("📦 Tìm mã Inactive")

if not st.session_state.get("_loaded"):
    st.info("**Bước 1:** Chọn điều kiện lọc.\n\n**Bước 2:** Bấm 🚀 **Tải dữ liệu**.")
    if load:
        st.session_state["_loaded"] = True
        st.rerun()
    st.stop()

if load:
    st.session_state.pop("_data", None)
    st.session_state["_loaded"] = True

# Tính ngày bắt đầu cho từng filter
def _from(n_days):
    return (bc_to - dt.timedelta(days=n_days - 1)).isoformat()

_to_str = bc_to.isoformat()

if "_data" not in st.session_state:
    with st.spinner("Đang truy vấn Odoo… (1–2 phút)"):

        # Toàn bộ mã
        all_prods = _search_read("product.product",
                                  [["active", "in", [True, False]]],
                                  ["id", "active", "create_date"])
        all_ids      = set(p["id"] for p in all_prods)
        inactive_ids = set(p["id"] for p in all_prods if not p["active"])
        create_date  = {p["id"]: (p.get("create_date") or "")[:10] for p in all_prods}

        # Tồn kho hiện tại
        quants = _search_read("stock.quant",
                               [["location_id.usage", "=", "internal"]],
                               ["product_id", "quantity"])
        stock = {}
        for q in quants:
            pid = q["product_id"][0]
            stock[pid] = stock.get(pid, 0) + q["quantity"]
        zero_stock = set(pid for pid in all_ids if round(stock.get(pid, 0), 4) == 0)

        # Doanh thu — Invoice
        from_ban = _from(n_ban) if f_khong_ban and n_ban > 0 else _from(60)
        inv_g = _search_read("account.move.line",
                              [["move_id.move_type", "=", "out_invoice"],
                               ["move_id.state", "=", "posted"],
                               ["move_id.invoice_date", ">=", from_ban],
                               ["move_id.invoice_date", "<=", _to_str],
                               ["product_id", "!=", False]],
                              ["product_id"], groupby=["product_id"])
        sold = set(g["product_id"][0] for g in inv_g if g.get("product_id"))

        # Doanh thu — POS
        pos_g = _search_read("pos.order.line",
                              [["order_id.state", "in", ["done", "invoiced"]],
                               ["order_id.date_order", ">=", from_ban],
                               ["order_id.date_order", "<=", _to_str],
                               ["product_id", "!=", False]],
                              ["product_id"], groupby=["product_id"])
        sold |= set(g["product_id"][0] for g in pos_g if g.get("product_id"))

        # Nhập kho
        from_nhap = _from(n_nhap) if f_khong_nhap and n_nhap > 0 else _from(60)
        moves = _search_read("stock.move",
                              [["state", "=", "done"],
                               ["date", ">=", from_nhap + " 00:00:00"],
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
            "n_ban": n_ban, "n_nhap": n_nhap,
        }

d = st.session_state["_data"]

# ── Áp điều kiện lọc ─────────────────────────────────────────────────────────
candidate = set(d["all_ids"]) if f_tat_ca else d["all_ids"] - d["inactive_ids"]

if f_khong_ban:   candidate -= d["sold"]
if f_khong_ton:   candidate &= d["zero_stock"]
if f_khong_nhap:  candidate -= d["imported"]
if f_khong_moi and n_moi > 0:
    cutoff = _from(n_moi)
    candidate -= {pid for pid, cd in d["create_date"].items() if cd >= cutoff}

# Caption hiển thị N ngày đã dùng khi tải
n_ban_used  = d.get("n_ban", 60)
n_nhap_used = d.get("n_nhap", 60)
st.caption(
    f"Đến ngày: **{bc_to:%d/%m/%Y}** · "
    f"Bán: **{n_ban_used} ngày** · Nhập: **{n_nhap_used} ngày** · "
    f"Kết quả: **{len(candidate):,} mã**"
)

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
                "Có bán":       "✓" if pid in d["sold"]     else "",
                "Có nhập":      "✓" if pid in d["imported"] else "",
                "Ngày tạo":     d["create_date"].get(pid, ""),
            })

df = pd.DataFrame(rows).sort_values(["Trạng thái", "Mã vạch"]).reset_index(drop=True)

st.dataframe(df, width="stretch", hide_index=True, height=600)

st.download_button("⬇️ Tải Excel",
                   df.to_csv(index=False).encode("utf-8-sig"),
                   file_name=f"tim_ma_inactive_{bc_to:%Y%m%d}.csv",
                   mime="text/csv")
