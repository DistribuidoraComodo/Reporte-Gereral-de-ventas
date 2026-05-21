import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import timedelta

st.set_page_config(
    page_title="Reporte General de Ventas",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>.block-container { padding-top: 1.5rem; }</style>
""", unsafe_allow_html=True)

# ── Constantes ────────────────────────────────────────────────────────────────
INACTIVO_DIAS = 90
GERENCIA_PASSWORD = "gerencia2025"
EXCLUIR_CODIGOS = [29999, 50000, 100000, 20200, 20201, 20202, 20203]
CENTRO_ARG = {"lat": -34.6, "lon": -63.0}
MESES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
MESES_FULL = {1:"Enero",2:"Febrero",3:"Marzo",4:"Abril",5:"Mayo",6:"Junio",
              7:"Julio",8:"Agosto",9:"Septiembre",10:"Octubre",11:"Noviembre",12:"Diciembre"}


# ── Carga de datos ────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Cargando datos...")
def cargar_datos(archivo):
    import io
    if isinstance(archivo, (bytes, bytearray)):
        archivo = io.BytesIO(archivo)
    # -- Hoja Ventas --
    df_v = pd.read_excel(archivo, sheet_name="Ventas")
    nombres_v = [
        "fecha","periodo","cod_cliente","cliente","cod_vendedor","vendedor",
        "cbte","t_cbte","pto_vta","n_cbte","cod_articulo","descripcion",
        "cantidad","facturacion","familia","rubro","subrubro","marca",
        "clasificacion","subclasificacion","localidad","provincia",
    ]
    df_v = df_v.iloc[:, :len(nombres_v)]
    df_v.columns = nombres_v
    df_v["fecha"] = pd.to_datetime(df_v["fecha"], errors="coerce")
    df_v = df_v.dropna(subset=["fecha","facturacion"])
    df_v["año"] = df_v["fecha"].dt.year
    df_v["mes"] = df_v["fecha"].dt.month
    df_v["cod_vendedor"] = pd.to_numeric(df_v["cod_vendedor"], errors="coerce")
    df_v["cod_cliente"] = pd.to_numeric(df_v["cod_cliente"], errors="coerce")

    # -- Hoja Base clientes --
    df_b = pd.read_excel(archivo, sheet_name="Base clientes")
    df_b.columns = [str(c).strip() for c in df_b.columns]
    # Mapear columnas clave
    df_b = df_b.rename(columns={
        "Código":         "cod_cliente",
        "Razón Social":   "razon_social",
        "Nombre":         "nombre_fantasia",
        "Vendedor":       "vendedor_asignado",
        "Localidad":      "localidad",
        "Provincia":      "provincia",
        "Mail":           "mail",
        "Telefono":       "telefono",
        "Clasificacion":  "clasificacion",
        "SubClasificacion":"subclasificacion",
        "Zona":           "zona",
        "Fechaalta":      "fecha_alta",
        "Fechabaja":      "fecha_baja",
    })
    df_b["cod_cliente"] = pd.to_numeric(df_b["cod_cliente"], errors="coerce")
    df_b = df_b.dropna(subset=["cod_cliente"])
    if "fecha_alta" in df_b.columns:
        df_b["fecha_alta"] = pd.to_datetime(df_b["fecha_alta"], errors="coerce")
    # Extraer código de vendedor del texto "APELLIDO (20004)"
    df_b["cod_vendedor"] = (
        df_b["vendedor_asignado"]
        .str.extract(r'\((\d+)\)')
        .squeeze()
        .pipe(pd.to_numeric, errors="coerce")
    )
    return df_v, df_b


@st.cache_data(show_spinner="Cargando coordenadas...")
def cargar_coords(archivo):
    import io
    if isinstance(archivo, (bytes, bytearray)):
        archivo = io.BytesIO(archivo)
    df = pd.read_excel(archivo)
    cols_orig = list(df.columns)
    df.columns = [str(c).strip() for c in df.columns]

    col_map = {}
    for c in df.columns:
        lc = c.lower()
        if ("cod" in lc and "cli" in lc) or lc in ("código", "codigo", "id"):
            if "cod_cliente" not in col_map.values():
                col_map[c] = "cod_cliente"
        elif "raz" in lc or "social" in lc:
            col_map[c] = "razon_social"
        elif "lat" in lc:
            col_map[c] = "latitud"
        elif "lon" in lc or "lng" in lc:
            col_map[c] = "longitud"

    df = df.rename(columns=col_map)

    # Validar columnas necesarias
    faltantes = [c for c in ["cod_cliente", "latitud", "longitud"] if c not in df.columns]
    if faltantes:
        st.error(
            f"⚠️ El archivo de coordenadas no tiene las columnas necesarias: **{', '.join(faltantes)}**.\n\n"
            f"El archivo debe tener columnas de **código de cliente**, **latitud** y **longitud**.\n\n"
            f"Columnas encontradas: {cols_orig}"
        )
        st.stop()

    df["cod_cliente"] = pd.to_numeric(df["cod_cliente"], errors="coerce")
    df["latitud"]     = pd.to_numeric(df["latitud"],     errors="coerce")
    df["longitud"]    = pd.to_numeric(df["longitud"],    errors="coerce")
    return df.dropna(subset=["cod_cliente","latitud","longitud"])


def get_vendedores(df_base):
    """Lista de vendedores reales desde la base de clientes."""
    mask = ~df_base["cod_vendedor"].isin(EXCLUIR_CODIGOS) & df_base["cod_vendedor"].notna()
    v = (
        df_base[mask][["cod_vendedor","vendedor_asignado"]]
        .drop_duplicates()
        .sort_values("vendedor_asignado")
    )
    return v


@st.cache_data(show_spinner="Calculando resumen de clientes...")
def resumen_clientes(df_base_cod, df_ventas_cod, hoy_str):
    """
    Cruza Base clientes con Ventas.
    Todos los clientes de la base aparecen; los sin ventas recientes = INACTIVO.
    """
    hoy       = pd.Timestamp(hoy_str)
    hace_90   = hoy - timedelta(days=90)
    hace_180  = hoy - timedelta(days=180)
    año_actual = hoy.year

    # Pre-agrupar ventas por cliente para performance
    ventas_grp = dict(tuple(df_ventas_cod.groupby("cod_cliente")))

    filas = []
    for _, row in df_base_cod.iterrows():
        cod     = row["cod_cliente"]
        nombre  = row.get("razon_social") or row.get("nombre_fantasia") or str(cod)
        localidad = row.get("localidad", "-") or "-"
        provincia = row.get("provincia", "-") or "-"
        mail      = row.get("mail", "") or ""
        telefono  = row.get("telefono", "") or ""

        g = ventas_grp.get(cod)

        if g is None or g.empty:
            # Sin ventas en el período
            ultima         = None
            dias           = 9999
            estado         = "SIN COMPRAS"
            fact_3m        = 0.0
            fact_3m_ant    = 0.0
            tendencia      = 0.0
            fact_año       = 0.0
            marcas_str     = "-"
        else:
            ultima      = g["fecha"].max()
            dias        = (hoy - ultima).days
            estado      = "INACTIVO" if dias > INACTIVO_DIAS else "ACTIVO"
            fact_3m     = g[g["fecha"] >= hace_90]["facturacion"].sum()
            fact_3m_ant = g[(g["fecha"] >= hace_180) & (g["fecha"] < hace_90)]["facturacion"].sum()
            if fact_3m_ant > 0:
                tendencia = (fact_3m - fact_3m_ant) / fact_3m_ant * 100
            elif fact_3m > 0:
                tendencia = 100.0
            else:
                tendencia = 0.0
            fact_año    = g[g["año"] == año_actual]["facturacion"].sum()
            marcas_lista = sorted(g["marca"].dropna().unique())
            marcas_str   = ", ".join(marcas_lista[:3])
            if len(marcas_lista) > 3:
                marcas_str += f" (+{len(marcas_lista)-3})"

        filas.append({
            "cod_cliente":    cod,
            "cliente":        nombre,
            "ultima_compra":  ultima,
            "dias_sin_compra": dias,
            "estado":         estado,
            "fact_3m":        fact_3m,
            "tendencia":      tendencia,
            "fact_año":       fact_año,
            "marcas":         marcas_str,
            "localidad":      localidad,
            "provincia":      provincia,
            "mail":           mail,
            "telefono":       telefono,
        })

    return pd.DataFrame(filas)


# ── Helpers de formato ────────────────────────────────────────────────────────
def fmt_peso(v):
    return f"${v:,.0f}".replace(",", ".")

def fmt_tendencia(v):
    if v > 5:   return f"▲ {v:.1f}%"
    elif v < -5: return f"▼ {abs(v):.1f}%"
    else:        return f"→ {v:.1f}%"

def fmt_ultima(ts):
    if ts is None or pd.isna(ts): return "Sin compras"
    return ts.strftime("%d/%m/%Y")

def fmt_dias(d):
    if d == 9999: return "—"
    return str(d)

def fmt_compacto(v):
    """Formato abreviado para deltas de métricas: $1.2M, $450K, etc."""
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    elif v >= 1_000:
        return f"${v/1_000:.0f}K"
    return fmt_peso(v)

def tab_ventas_articulos(ventas_df, key_prefix=""):
    """Reporte de ventas por artículo: unidades, facturación, promedios y evolución."""

    fecha_min = ventas_df["fecha"].min().date()
    fecha_max = ventas_df["fecha"].max().date()

    # ── Filtros ───────────────────────────────────────────────────────────────
    with st.expander("🔽 Filtros", expanded=True):
        fc1, fc2 = st.columns(2)
        with fc1:
            desde_a = st.date_input("Desde", value=fecha_min, min_value=fecha_min,
                                    max_value=fecha_max, key=f"{key_prefix}_art_desde")
        with fc2:
            hasta_a = st.date_input("Hasta", value=fecha_max, min_value=fecha_min,
                                    max_value=fecha_max, key=f"{key_prefix}_art_hasta")

        ff1, ff2, ff3, ff4 = st.columns(4)
        marcas_art   = sorted(ventas_df["marca"].dropna().unique())
        familias_art = sorted(ventas_df["familia"].dropna().unique())
        rubros_art   = sorted(ventas_df["rubro"].dropna().unique())

        sel_marca_a  = ff1.multiselect("Marca",   marcas_art,   key=f"{key_prefix}_art_marca",  placeholder="Todas")
        sel_fam_a    = ff2.multiselect("Familia", familias_art, key=f"{key_prefix}_art_fam",    placeholder="Todas")
        sel_rub_a    = ff3.multiselect("Rubro",   rubros_art,   key=f"{key_prefix}_art_rub",    placeholder="Todos")
        busq_art     = ff4.text_input("Buscar artículo:", placeholder="Nombre o código",
                                      key=f"{key_prefix}_art_busq")

    if desde_a > hasta_a:
        st.error("La fecha 'Desde' no puede ser mayor que 'Hasta'.")
        return

    # ── Aplicar filtros ───────────────────────────────────────────────────────
    vf = ventas_df[(ventas_df["fecha"] >= pd.Timestamp(desde_a)) &
                   (ventas_df["fecha"] <= pd.Timestamp(hasta_a))].copy()
    if sel_marca_a: vf = vf[vf["marca"].isin(sel_marca_a)]
    if sel_fam_a:   vf = vf[vf["familia"].isin(sel_fam_a)]
    if sel_rub_a:   vf = vf[vf["rubro"].isin(sel_rub_a)]
    if busq_art:
        bl = busq_art.strip().lower()
        vf = vf[vf["descripcion"].str.lower().str.contains(bl, na=False) |
                vf["cod_articulo"].astype(str).str.lower().str.contains(bl, na=False)]

    if vf.empty:
        st.warning("No hay ventas para los filtros seleccionados.")
        return

    # ── Calcular meses en el período para promedios ───────────────────────────
    meses_periodo = max(1, (pd.Timestamp(hasta_a).to_period("M") -
                            pd.Timestamp(desde_a).to_period("M")).n + 1)

    # ── Tabla principal por artículo ──────────────────────────────────────────
    grp = vf.groupby(["cod_articulo","descripcion","marca","familia","rubro"]).agg(
        unidades   =("cantidad",    "sum"),
        facturacion=("facturacion", "sum"),
        clientes   =("cod_cliente", "nunique"),
        meses_con_venta=("mes",     "nunique"),
    ).reset_index()

    grp["prom_mens_uds"]  = (grp["unidades"]    / meses_periodo).round(1)
    grp["prom_mens_fact"] = (grp["facturacion"] / meses_periodo).round(0)
    grp["precio_prom"]    = (grp["facturacion"] / grp["unidades"].where(grp["unidades"] != 0)).round(2)

    # KPIs rápidos
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("📦 Artículos distintos",   f"{len(grp):,}")
    k2.metric("📊 Total unidades",        f"{grp['unidades'].sum():,.0f}")
    k3.metric("💰 Facturación total",     fmt_peso(grp["facturacion"].sum()))
    k4.metric("👥 Clientes distintos",    f"{vf['cod_cliente'].nunique():,}")

    st.markdown("---")

    # Ordenamiento
    ord_col = st.radio("Ordenar por:", ["Unidades","Facturación","Precio promedio","Clientes"],
                       horizontal=True, key=f"{key_prefix}_art_orden")
    ord_map = {"Unidades":"unidades","Facturación":"facturacion",
               "Precio promedio":"precio_prom","Clientes":"clientes"}
    grp = grp.sort_values(ord_map[ord_col], ascending=False).reset_index(drop=True)
    grp.insert(0, "#", range(1, len(grp)+1))

    tbl = grp.copy()
    tbl["unidades"]        = tbl["unidades"].apply(lambda x: f"{x:,.0f}")
    tbl["facturacion"]     = tbl["facturacion"].apply(fmt_peso)
    tbl["prom_mens_uds"]   = tbl["prom_mens_uds"].apply(lambda x: f"{x:,.1f}")
    tbl["prom_mens_fact"]  = tbl["prom_mens_fact"].apply(fmt_peso)
    tbl["precio_prom"]     = tbl["precio_prom"].apply(lambda x: fmt_peso(x) if pd.notna(x) else "—")
    tbl["clientes"]        = tbl["clientes"].astype(int)
    tbl["meses_con_venta"] = tbl["meses_con_venta"].astype(int)
    tbl.columns = ["#","Código","Descripción","Marca","Familia","Rubro",
                   "Unidades","Facturación","Clientes","Meses c/venta",
                   "Prom. mens. (uds)","Prom. mens. ($)","Precio prom."]
    st.dataframe(tbl, use_container_width=True, hide_index=True)

    st.download_button("📥 Descargar tabla",
                       tbl.to_csv(index=False).encode("utf-8"),
                       file_name=f"ventas_articulos_{key_prefix}.csv",
                       mime="text/csv", key=f"{key_prefix}_art_dl")

    # ── Detalle mensual de un artículo ────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 🔍 Evolución mensual de un artículo")
    arts_lista = grp["descripcion"].tolist()
    art_sel = st.selectbox("Seleccioná un artículo:", arts_lista,
                           key=f"{key_prefix}_art_det_sel")

    cod_art_sel = grp[grp["descripcion"] == art_sel]["cod_articulo"].iloc[0]
    vf_art = ventas_df[ventas_df["cod_articulo"] == cod_art_sel].copy()
    vf_art["periodo"] = pd.to_datetime(
        vf_art["año"].astype(str) + "-" + vf_art["mes"].astype(str).str.zfill(2) + "-01")

    evol = vf_art.groupby("periodo").agg(
        unidades   =("cantidad",    "sum"),
        facturacion=("facturacion", "sum"),
        clientes   =("cod_cliente", "nunique"),
    ).reset_index().sort_values("periodo")

    # Promedio mensual histórico
    prom_uds  = evol["unidades"].mean()
    prom_fact = evol["facturacion"].mean()

    da1, da2, da3 = st.columns(3)
    da1.metric("Prom. mensual (uds)",  f"{prom_uds:,.1f}")
    da2.metric("Prom. mensual ($)",    fmt_peso(prom_fact))
    da3.metric("Meses con venta",      len(evol))

    col_g1, col_g2 = st.columns(2)
    with col_g1:
        fig_u = px.bar(evol, x="periodo", y="unidades",
                       title="Unidades por mes",
                       labels={"periodo":"","unidades":"Unidades"},
                       color_discrete_sequence=["#0066cc"])
        fig_u.add_hline(y=prom_uds, line_dash="dash", line_color="orange",
                        annotation_text=f"Prom: {prom_uds:,.1f}")
        fig_u.update_layout(xaxis_tickformat="%b %Y")
        st.plotly_chart(fig_u, use_container_width=True)
    with col_g2:
        fig_f = px.bar(evol, x="periodo", y="facturacion",
                       title="Facturación por mes",
                       labels={"periodo":"","facturacion":"Facturación ($)"},
                       color_discrete_sequence=["#28a745"])
        fig_f.add_hline(y=prom_fact, line_dash="dash", line_color="orange",
                        annotation_text=f"Prom: {fmt_compacto(prom_fact)}")
        fig_f.update_layout(xaxis_tickformat="%b %Y")
        st.plotly_chart(fig_f, use_container_width=True)

    # Tabla mensual del artículo
    tbl_evol = evol.copy()
    tbl_evol["periodo"]     = tbl_evol["periodo"].dt.strftime("%b %Y")
    tbl_evol["unidades"]    = tbl_evol["unidades"].apply(lambda x: f"{x:,.0f}")
    tbl_evol["facturacion"] = tbl_evol["facturacion"].apply(fmt_peso)
    tbl_evol["clientes"]    = tbl_evol["clientes"].astype(int)
    tbl_evol.columns = ["Período","Unidades","Facturación","Clientes"]
    st.dataframe(tbl_evol, use_container_width=True, hide_index=True)

    # ── Ranking de clientes del artículo ─────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 👥 Clientes que más compran este artículo")
    st.caption("Aplica el período y filtros seleccionados arriba.")

    vf_art_filt = vf[vf["cod_articulo"] == cod_art_sel]
    if vf_art_filt.empty:
        st.info("No hay clientes en el período/filtros seleccionados para este artículo.")
    else:
        crit_cli = st.radio("Ordenar por:", ["Unidades", "Facturación"],
                            horizontal=True, key=f"{key_prefix}_art_cli_crit")
        col_cli_ord = "unidades" if crit_cli == "Unidades" else "facturacion"

        top_cli = (
            vf_art_filt.groupby("cod_cliente").agg(
                unidades     =("cantidad",    "sum"),
                facturacion  =("facturacion", "sum"),
                ultima_compra=("fecha",       "max"),
                cliente      =("cliente",     "first"),
            ).reset_index()
            .sort_values(col_cli_ord, ascending=False)
            .reset_index(drop=True)
        )
        top_cli.insert(0, "#", range(1, len(top_cli)+1))

        # Gráfico top 10
        top10_cli = top_cli.head(10)
        fig_cli = px.bar(
            top10_cli, x=col_cli_ord, y="cliente", orientation="h",
            title=f"Top 10 clientes — {art_sel[:50]}",
            labels={col_cli_ord: crit_cli, "cliente": ""},
            color_discrete_sequence=["#0066cc"],
            text=top10_cli[col_cli_ord].apply(
                lambda x: f"{x:,.0f}" if crit_cli == "Unidades" else fmt_compacto(x))
        )
        fig_cli.update_traces(textposition="outside")
        fig_cli.update_layout(yaxis={"categoryorder": "total ascending"}, margin=dict(t=40, r=100))
        st.plotly_chart(fig_cli, use_container_width=True)

        # Tabla completa
        tbl_cli = top_cli.copy()
        tbl_cli["unidades"]      = tbl_cli["unidades"].apply(lambda x: f"{x:,.0f}")
        tbl_cli["facturacion"]   = tbl_cli["facturacion"].apply(fmt_peso)
        tbl_cli["ultima_compra"] = tbl_cli["ultima_compra"].dt.strftime("%d/%m/%Y")
        tbl_cli = tbl_cli[["#","cliente","unidades","facturacion","ultima_compra"]]
        tbl_cli.columns = ["#","Cliente","Unidades","Facturación","Última compra"]
        st.dataframe(tbl_cli, use_container_width=True, hide_index=True)

        st.download_button(
            "📥 Descargar ranking de clientes",
            tbl_cli.to_csv(index=False).encode("utf-8"),
            file_name=f"clientes_{cod_art_sel}.csv",
            mime="text/csv",
            key=f"{key_prefix}_art_cli_dl"
        )


def resumen_clasificacion(base_df, ventas_df, resumen_df=None):
    """
    Muestra un resumen de clientes agrupado por Clasificación y Subclasificación.
    base_df: base de clientes del vendedor/gerencia
    ventas_df: ventas para calcular facturación por clasificación
    resumen_df: resumen de estados (opcional, para mostrar activos/inactivos)
    """
    cols_clasif = ["cod_cliente","clasificacion","subclasificacion"]
    clasif = base_df[[c for c in cols_clasif if c in base_df.columns]].drop_duplicates("cod_cliente").copy()
    if "clasificacion" not in clasif.columns:
        st.info("No hay datos de clasificación en la base de clientes.")
        return

    clasif["clasificacion"]    = clasif["clasificacion"].fillna("Sin clasificación")
    clasif["subclasificacion"] = clasif.get("subclasificacion", pd.Series(dtype=str)).fillna("Sin subclasificación") if "subclasificacion" in clasif.columns else "—"

    # Facturación por clasificación cruzando con ventas
    # Eliminamos clasificacion/subclasificacion de ventas para evitar duplicados al mergear con la base
    cols_drop = [c for c in ["clasificacion","subclasificacion"] if c in ventas_df.columns]
    ventas_c = ventas_df.drop(columns=cols_drop).merge(clasif, on="cod_cliente", how="left")
    ventas_c["clasificacion"]    = ventas_c["clasificacion"].fillna("Sin clasificación")
    ventas_c["subclasificacion"] = ventas_c["subclasificacion"].fillna("Sin subclasificación") if "subclasificacion" in ventas_c.columns else "—"

    grp_cols = ["clasificacion","subclasificacion"] if "subclasificacion" in clasif.columns else ["clasificacion"]

    # Clientes en base por clasificación
    cnt = clasif.groupby(grp_cols)["cod_cliente"].nunique().reset_index(name="clientes")

    # Facturación
    fact = ventas_c.groupby(grp_cols)["facturacion"].sum().reset_index(name="facturacion")
    tbl = cnt.merge(fact, on=grp_cols, how="left").fillna(0)

    # Activos/Inactivos si tenemos resumen
    if resumen_df is not None:
        res_c = resumen_df.merge(clasif, on="cod_cliente", how="left")
        res_c["clasificacion"]    = res_c["clasificacion"].fillna("Sin clasificación")
        res_c["subclasificacion"] = res_c["subclasificacion"].fillna("Sin subclasificación") if "subclasificacion" in res_c.columns else "—"
        estados = res_c.groupby(grp_cols).apply(
            lambda x: pd.Series({
                "activos":     (x["estado"]=="ACTIVO").sum(),
                "inactivos":   (x["estado"]=="INACTIVO").sum(),
                "sin_compras": (x["estado"]=="SIN COMPRAS").sum(),
            })
        ).reset_index()
        tbl = tbl.merge(estados, on=grp_cols, how="left").fillna(0)
        tbl[["activos","inactivos","sin_compras"]] = tbl[["activos","inactivos","sin_compras"]].astype(int)

    tbl = tbl.sort_values(grp_cols)
    tbl["facturacion"] = tbl["facturacion"].apply(fmt_peso)
    tbl["clientes"]    = tbl["clientes"].astype(int)

    rename = {"clasificacion":"Clasificación","subclasificacion":"Subclasificación",
               "clientes":"Clientes","facturacion":"Facturación año",
               "activos":"✅ Activos","inactivos":"⚠️ Inactivos","sin_compras":"❌ Sin compras"}
    tbl = tbl.rename(columns=rename)
    st.dataframe(tbl, use_container_width=True, hide_index=True)


def tabla_mensual(df_f):
    pivot = (
        df_f.groupby(["año","mes"])["facturacion"]
        .sum().unstack("año").fillna(0)
    )
    pivot.index = [MESES_FULL.get(i, i) for i in pivot.index]
    total = pivot.sum().rename("TOTAL")
    pivot = pd.concat([pivot, pd.DataFrame([total])])
    for col in pivot.columns:
        pivot[col] = pivot[col].apply(fmt_peso)
    return pivot


@st.cache_data(show_spinner="Calculando mix de marcas...")
def mix_marcas_clientes(df_ventas_cod, df_base_cod):
    """
    Por cada cliente calcula el % de facturación por marca.
    Retorna un DataFrame con: cod_cliente, cliente, marca, facturacion, pct_marca, marcas_totales
    """
    # Solo clientes con ventas
    grp = df_ventas_cod.groupby(["cod_cliente","marca"])["facturacion"].sum().reset_index()
    grp = grp[grp["facturacion"] > 0]
    total = grp.groupby("cod_cliente")["facturacion"].sum().rename("total").reset_index()
    grp = grp.merge(total, on="cod_cliente")
    grp["pct_marca"] = grp["facturacion"] / grp["total"] * 100
    # Cantidad de marcas distintas por cliente
    n_marcas = grp.groupby("cod_cliente")["marca"].nunique().rename("n_marcas").reset_index()
    grp = grp.merge(n_marcas, on="cod_cliente")
    # Agregar nombre de cliente
    nombres = df_base_cod[["cod_cliente","razon_social"]].drop_duplicates("cod_cliente")
    grp = grp.merge(nombres, on="cod_cliente", how="left")
    grp["cliente"] = grp["razon_social"].fillna(grp["cod_cliente"].astype(str))
    return grp


def tab_analisis_marcas(ventas_df, base_df, key_prefix="", vendedores_disponibles=None):
    """Contenido de la pestaña de análisis de marcas (reutilizable en vendedor y gerencia)."""

    # ── Filtros superiores ──
    fecha_min = ventas_df["fecha"].min().date()
    fecha_max = ventas_df["fecha"].max().date()

    col_f1, col_f2, col_f3 = st.columns([1, 1, 2])
    with col_f1:
        desde_m = st.date_input("Desde", value=fecha_min,
                                min_value=fecha_min, max_value=fecha_max,
                                key=f"{key_prefix}_desde_marca")
    with col_f2:
        hasta_m = st.date_input("Hasta", value=fecha_max,
                                min_value=fecha_min, max_value=fecha_max,
                                key=f"{key_prefix}_hasta_marca")
    with col_f3:
        st.caption(f"Disponible: {fecha_min.strftime('%d/%m/%Y')} → {fecha_max.strftime('%d/%m/%Y')}")
        st.caption(f"✅ Período seleccionado: **{desde_m.strftime('%d/%m/%Y')} → {hasta_m.strftime('%d/%m/%Y')}**")

    if desde_m > hasta_m:
        st.error("La fecha 'Desde' no puede ser mayor que 'Hasta'.")
        return

    # Filtrar ventas al período Y solo clientes que están en la base (respeta filtro de clasificación)
    cods_en_base = set(base_df["cod_cliente"].dropna().unique())
    ventas_periodo = ventas_df[
        (ventas_df["fecha"] >= pd.Timestamp(desde_m)) &
        (ventas_df["fecha"] <= pd.Timestamp(hasta_m)) &
        (ventas_df["cod_cliente"].isin(cods_en_base))
    ]
    if ventas_periodo.empty:
        st.warning("No hay ventas en el período seleccionado.")
        return

    st.divider()
    mix = mix_marcas_clientes(ventas_periodo, base_df)

    if mix.empty:
        st.info("No hay datos de ventas para analizar marcas.")
        return

    # Agregar vendedor al mix si está disponible en base
    if vendedores_disponibles is not None:
        vend_map = base_df[["cod_cliente","vendedor_asignado"]].drop_duplicates("cod_cliente")
        mix = mix.merge(vend_map, on="cod_cliente", how="left")

    marcas_disp = sorted(mix["marca"].dropna().unique().tolist())

    col1, col2 = st.columns([2, 1])
    with col1:
        marca_sel = st.selectbox("Seleccioná una marca:", marcas_disp, key=f"{key_prefix}_marca")
    with col2:
        exclusividad = st.slider(
            "Exclusividad mínima (%)", min_value=10, max_value=100, value=100, step=5,
            key=f"{key_prefix}_excl",
            help="100% = solo compra esa marca. 80% = al menos el 80% de sus compras son de esa marca."
        )

    marca_data = mix[mix["marca"] == marca_sel].copy()
    filtrados  = marca_data[marca_data["pct_marca"] >= exclusividad].sort_values("pct_marca", ascending=False)

    # ── Filtro por vendedor (solo gerencia) ──
    if vendedores_disponibles is not None and not filtrados.empty and "vendedor_asignado" in filtrados.columns:
        vends_en_resultado = sorted(filtrados["vendedor_asignado"].dropna().unique().tolist())
        sel_vend_marca = st.multiselect(
            "Filtrar por vendedor:",
            options=vends_en_resultado,
            default=[],
            placeholder="Todos los vendedores",
            key=f"{key_prefix}_vend_marca"
        )
        if sel_vend_marca:
            filtrados = filtrados[filtrados["vendedor_asignado"].isin(sel_vend_marca)]

    label = "exclusivamente" if exclusividad == 100 else f"≥{exclusividad}% de"
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Clientes encontrados", len(filtrados))
    col_b.metric(f"Facturación total ({marca_sel})", fmt_peso(filtrados["facturacion"].sum()))
    col_c.metric("Promedio exclusividad", f"{filtrados['pct_marca'].mean():.1f}%" if not filtrados.empty else "—")

    if filtrados.empty:
        st.info(f"No hay clientes que compren {marca_sel} con al menos {exclusividad}% de exclusividad.")
        return

    # Tabla principal
    cols_tbl = ["cliente","pct_marca","facturacion","n_marcas"]
    if vendedores_disponibles is not None and "vendedor_asignado" in filtrados.columns:
        cols_tbl.insert(1, "vendedor_asignado")
    tbl = filtrados[cols_tbl].copy()
    tbl["pct_marca"]   = tbl["pct_marca"].apply(lambda x: f"{x:.1f}%")
    tbl["facturacion"] = tbl["facturacion"].apply(fmt_peso)
    tbl["n_marcas"]    = tbl["n_marcas"].apply(lambda x: f"{x} marca{'s' if x>1 else ''}")
    rename = {"cliente":"Cliente","pct_marca":f"% {marca_sel}","facturacion":f"Fact. {marca_sel}",
              "n_marcas":"Marcas que compra","vendedor_asignado":"Vendedor"}
    tbl = tbl.rename(columns=rename)
    st.dataframe(tbl, use_container_width=True, hide_index=True)

    st.download_button(
        "📥 Descargar lista",
        tbl.to_csv(index=False).encode("utf-8"),
        file_name=f"clientes_{marca_sel.strip().replace(' ','_')}_{exclusividad}pct.csv",
        mime="text/csv",
        key=f"{key_prefix}_dl"
    )

    # ── Mix completo por cliente ──
    st.markdown("---")
    st.markdown(f"#### Mix completo de marcas por cliente")

    # Filtro por vendedor para el mix (solo gerencia)
    filtrados_mix = filtrados.copy()
    if vendedores_disponibles is not None and "vendedor_asignado" in filtrados_mix.columns:
        vends_mix = sorted(filtrados_mix["vendedor_asignado"].dropna().unique().tolist())
        sel_vend_mix = st.multiselect(
            "Filtrar por vendedor (mix):",
            options=vends_mix, default=[],
            placeholder="Todos",
            key=f"{key_prefix}_vend_mix"
        )
        if sel_vend_mix:
            filtrados_mix = filtrados_mix[filtrados_mix["vendedor_asignado"].isin(sel_vend_mix)]

    cliente_sel = st.selectbox(
        "Ver mix de marcas de un cliente:",
        ["(Seleccioná un cliente)"] + filtrados_mix["cliente"].tolist(),
        key=f"{key_prefix}_cli_sel"
    )
    if cliente_sel != "(Seleccioná un cliente)":
        cod_sel_mix = filtrados_mix[filtrados_mix["cliente"] == cliente_sel]["cod_cliente"].iloc[0]
        mix_cli = mix[mix["cod_cliente"] == cod_sel_mix].sort_values("pct_marca", ascending=False)
        fig = px.bar(
            mix_cli, x="pct_marca", y="marca", orientation="h",
            title=f"Mix de marcas — {cliente_sel}",
            labels={"pct_marca": "% de facturación", "marca": ""},
            color="marca",
            text=mix_cli["pct_marca"].apply(lambda x: f"{x:.1f}%"),
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    # Distribución de exclusividad
    st.markdown(f"#### Distribución — ¿qué % destinan a {marca_sel}?")
    bins = [0, 25, 50, 75, 90, 100]
    labels_bins = ["1-25%", "26-50%", "51-75%", "76-90%", "91-100%"]
    marca_data2 = marca_data.copy()
    marca_data2["rango"] = pd.cut(marca_data2["pct_marca"], bins=bins, labels=labels_bins, include_lowest=True)
    dist = marca_data2.groupby("rango", observed=True).size().reset_index(name="clientes")
    fig2 = px.bar(dist, x="rango", y="clientes",
                  title=f"¿Qué % de sus compras destinan a {marca_sel}?",
                  labels={"rango":"Exclusividad","clientes":"Cantidad de clientes"},
                  color_discrete_sequence=["#0066cc"], text="clientes")
    fig2.update_traces(textposition="outside")
    fig2.update_layout(margin=dict(t=40))
    st.plotly_chart(fig2, use_container_width=True)


def tab_mix_cliente(ventas_df, base_df, key_prefix=""):
    """Pestaña: buscar un cliente y ver su mix, KPIs, top artículos y detalle por artículo."""
    fecha_min = ventas_df["fecha"].min().date()
    fecha_max = ventas_df["fecha"].max().date()
    default_desde = max(fecha_min, (pd.Timestamp(fecha_max) - pd.DateOffset(months=12)).date())

    # ── Fechas ────────────────────────────────────────────────────────────────
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        desde_mix = st.date_input("Desde", value=default_desde, min_value=fecha_min, max_value=fecha_max,
                                  key=f"{key_prefix}_mix_desde")
    with col_d2:
        hasta_mix = st.date_input("Hasta", value=fecha_max, min_value=fecha_min, max_value=fecha_max,
                                  key=f"{key_prefix}_mix_hasta")
    st.caption(f"✅ Período: **{desde_mix.strftime('%d/%m/%Y')} → {hasta_mix.strftime('%d/%m/%Y')}**")

    if desde_mix > hasta_mix:
        st.error("La fecha 'Desde' no puede ser mayor que 'Hasta'.")
        return

    # ── Buscador con botón limpiar ────────────────────────────────────────────
    st.markdown("#### 🔍 Buscar cliente")
    key_busq = f"{key_prefix}_busq_cli"

    def _limpiar_busq():
        st.session_state[key_busq] = ""

    col_inp, col_btn = st.columns([5, 1])
    with col_btn:
        st.button("🗑️ Limpiar", key=f"{key_prefix}_limpiar",
                  on_click=_limpiar_busq, use_container_width=True)
    with col_inp:
        busqueda = st.text_input("Nombre o código:", placeholder="Ej: GARCIA o 7088",
                                 key=key_busq, label_visibility="collapsed")

    if not busqueda or not busqueda.strip():
        st.info("Ingresá el nombre o código del cliente para ver su análisis.")
        return

    # ── Búsqueda en base y ventas ─────────────────────────────────────────────
    busqueda_lower = busqueda.strip().lower()
    cols_base = ["cod_cliente", "razon_social", "vendedor_asignado"]
    for col in ["nombre_fantasia", "fecha_alta"]:
        if col in base_df.columns:
            cols_base.append(col)

    mask = base_df["cod_cliente"].astype(str).str.contains(busqueda_lower, na=False)
    for col in ["razon_social", "nombre_fantasia"]:
        if col in base_df.columns:
            mask |= base_df[col].fillna("").str.lower().str.contains(busqueda_lower, na=False)

    candidatos = base_df[mask][cols_base].drop_duplicates("cod_cliente").copy()

    # También buscar en la hoja de ventas
    mask_v = (
        ventas_df["cliente"].fillna("").str.lower().str.contains(busqueda_lower, na=False) |
        ventas_df["cod_cliente"].astype(str).str.contains(busqueda_lower, na=False)
    )
    cods_en_ventas = ventas_df[mask_v]["cod_cliente"].dropna().unique()
    cods_ya = set(candidatos["cod_cliente"].tolist())
    for cod in cods_en_ventas:
        if cod not in cods_ya:
            nombre_v = ventas_df[ventas_df["cod_cliente"] == cod]["cliente"].dropna()
            nombre_v = nombre_v.iloc[0] if not nombre_v.empty else str(cod)
            extra_row = {"cod_cliente": cod, "razon_social": nombre_v, "vendedor_asignado": "-"}
            candidatos = pd.concat([candidatos, pd.DataFrame([extra_row])], ignore_index=True)

    if candidatos.empty:
        st.warning(f"No se encontró ningún cliente con '{busqueda}'.")
        return

    # Nombre a mostrar: razon_social → nombre_fantasia → cod_cliente
    def _nombre(row):
        for col in ["razon_social", "nombre_fantasia"]:
            v = str(row.get(col, "") or "").strip()
            if v and v.lower() not in ("nan", "none", ""):
                return v
        return f"(cód. {int(row['cod_cliente'])})"

    candidatos["_display"] = candidatos.apply(_nombre, axis=1)
    candidatos = candidatos.sort_values("_display").reset_index(drop=True)

    st.caption(f"Se encontraron **{len(candidatos)}** cliente(s)")

    # Selectbox por índice — evita bugs cuando hay nombres repetidos
    # La key cambia con la búsqueda para resetear la selección al buscar algo nuevo
    sel_idx = st.selectbox(
        "Seleccioná el cliente:",
        options=list(range(len(candidatos))),
        format_func=lambda i: candidatos.iloc[i]["_display"],
        key=f"{key_prefix}_sel_cli_{busqueda_lower[:20]}"
    )
    cod_cli   = candidatos.iloc[sel_idx]["cod_cliente"]
    sel_nombre = candidatos.iloc[sel_idx]["_display"]

    # Ventas del cliente en el período seleccionado
    ventas_cli = ventas_df[
        (ventas_df["cod_cliente"] == cod_cli) &
        (ventas_df["fecha"] >= pd.Timestamp(desde_mix)) &
        (ventas_df["fecha"] <= pd.Timestamp(hasta_mix))
    ].copy()

    # Datos del cliente desde la base
    row_cli = candidatos[candidatos["cod_cliente"] == cod_cli].iloc[0]
    vendedor_info = row_cli.get("vendedor_asignado", "-") or "-"
    fecha_alta_val = None
    if "fecha_alta" in candidatos.columns:
        fa = row_cli.get("fecha_alta")
        fecha_alta_val = fa if (fa is not None and pd.notna(fa)) else None

    st.markdown(f"### {sel_nombre}")
    info_parts = [f"Vendedor: **{vendedor_info}**"]
    if fecha_alta_val is not None:
        try:
            info_parts.append(f"Cliente desde: **{pd.Timestamp(fecha_alta_val).strftime('%d/%m/%Y')}**")
        except Exception:
            pass
    st.caption("  |  ".join(info_parts))

    if ventas_cli.empty:
        st.warning(f"No tiene compras en el período {desde_mix.strftime('%d/%m/%Y')} → {hasta_mix.strftime('%d/%m/%Y')}.")
        return

    # ── KPIs principales (sobre todo el período, antes de filtros) ──
    total_periodo = ventas_cli["facturacion"].sum()
    ultima_compra = ventas_cli["fecha"].max()

    # Facturación último trimestre (90 días hasta hasta_mix)
    hace_90 = pd.Timestamp(hasta_mix) - timedelta(days=90)
    ventas_trim = ventas_cli[ventas_cli["fecha"] >= hace_90]
    fact_trim = ventas_trim["facturacion"].sum() if not ventas_trim.empty else 0.0

    # Mejor mes del período
    ventas_x_mes = ventas_cli.groupby(["año","mes"])["facturacion"].sum()
    if not ventas_x_mes.empty:
        mejor_idx  = ventas_x_mes.idxmax()
        mejor_fact = ventas_x_mes.max()
        mejor_mes_str = f"{MESES[mejor_idx[1]-1]} {mejor_idx[0]}"
    else:
        mejor_mes_str = "—"
        mejor_fact = 0.0

    k1, k2, k3 = st.columns(3)
    k1.metric("💰 Facturación total período", fmt_peso(total_periodo))
    k2.metric("📊 Facturación último trimestre", fmt_peso(fact_trim))
    k3.metric("🏆 Mejor mes", mejor_mes_str, fmt_compacto(mejor_fact))

    # Info secundaria en caption (más pequeña)
    n_marcas = int(ventas_cli["marca"].nunique())
    st.caption(
        f"📅 Última compra: **{ultima_compra.strftime('%d/%m/%Y')}**  |  "
        f"🏷️ Marcas distintas: **{n_marcas}**"
    )

    st.divider()

    # ── Filtros de análisis ──
    st.markdown("**Filtrar análisis por:**")
    fc1, fc2, fc3, fc4 = st.columns(4)
    marcas_disp    = sorted(ventas_cli["marca"].dropna().unique().tolist())
    familias_disp  = sorted(ventas_cli["familia"].dropna().unique().tolist())
    rubros_disp    = sorted(ventas_cli["rubro"].dropna().unique().tolist())
    subrubros_disp = sorted(ventas_cli["subrubro"].dropna().unique().tolist())

    with fc1:
        sel_marcas = st.multiselect("Marca:", marcas_disp, default=[], placeholder="Todas",
                                    key=f"{key_prefix}_f_marca")
    with fc2:
        sel_familia = st.multiselect("Familia:", familias_disp, default=[], placeholder="Todas",
                                     key=f"{key_prefix}_f_familia")
    with fc3:
        sel_rubro = st.multiselect("Rubro:", rubros_disp, default=[], placeholder="Todos",
                                   key=f"{key_prefix}_f_rubro")
    with fc4:
        sel_subrubro = st.multiselect("Subrubro:", subrubros_disp, default=[], placeholder="Todos",
                                      key=f"{key_prefix}_f_subrubro")

    ventas_filt = ventas_cli.copy()
    if sel_marcas:    ventas_filt = ventas_filt[ventas_filt["marca"].isin(sel_marcas)]
    if sel_familia:   ventas_filt = ventas_filt[ventas_filt["familia"].isin(sel_familia)]
    if sel_rubro:     ventas_filt = ventas_filt[ventas_filt["rubro"].isin(sel_rubro)]
    if sel_subrubro:  ventas_filt = ventas_filt[ventas_filt["subrubro"].isin(sel_subrubro)]

    if ventas_filt.empty:
        st.warning("No hay ventas para los filtros seleccionados.")
        return

    # ── Mix de marcas ──
    st.markdown("#### Mix de marcas")
    mix_cli = ventas_filt.groupby("marca")["facturacion"].sum().reset_index()
    mix_cli = mix_cli[mix_cli["facturacion"] > 0].sort_values("facturacion", ascending=False)
    total_filt = mix_cli["facturacion"].sum()
    mix_cli["pct"] = mix_cli["facturacion"] / total_filt * 100

    col_g, col_t = st.columns([2, 1])
    with col_g:
        fig = px.bar(mix_cli, x="pct", y="marca", orientation="h",
                     title="Mix de marcas",
                     labels={"pct": "% de facturación", "marca": ""},
                     color="marca",
                     text=mix_cli["pct"].apply(lambda x: f"{x:.1f}%"))
        fig.update_traces(textposition="outside")
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False, margin=dict(t=30, r=80))
        st.plotly_chart(fig, use_container_width=True)
    with col_t:
        tbl_mix = mix_cli[["marca","facturacion","pct"]].copy()
        tbl_mix["facturacion"] = tbl_mix["facturacion"].apply(fmt_peso)
        tbl_mix["pct"] = tbl_mix["pct"].apply(lambda x: f"{x:.1f}%")
        tbl_mix.columns = ["Marca", "Facturación", "% del total"]
        st.dataframe(tbl_mix, use_container_width=True, hide_index=True)

    # ── Evolución mensual por marca ──
    st.markdown("#### Evolución mensual por marca")
    evol_marc = ventas_filt.groupby(["año","mes","marca"])["facturacion"].sum().reset_index()
    evol_marc["periodo"] = pd.to_datetime(
        evol_marc["año"].astype(str) + "-" + evol_marc["mes"].astype(str).str.zfill(2) + "-01")
    fig2 = px.bar(evol_marc.sort_values("periodo"), x="periodo", y="facturacion", color="marca",
                  labels={"periodo": "", "facturacion": "Facturación ($)", "marca": "Marca"})
    fig2.update_layout(xaxis_tickformat="%b %Y", hovermode="x unified")
    st.plotly_chart(fig2, use_container_width=True)

    # ── Top 10 artículos más comprados ──
    st.markdown("---")
    st.markdown("#### 📦 Top 10 artículos más comprados")
    st.caption("Los filtros de marca, familia, rubro y subrubro aplican a este listado.")

    top10_col1, top10_col2, top10_col3 = st.columns([2, 2, 1])
    with top10_col1:
        busq_top10 = st.text_input("Buscar artículo:", placeholder="Nombre o código...",
                                    key=f"{key_prefix}_top10_busq")
    with top10_col2:
        marcas_disp = sorted(ventas_filt["marca"].dropna().unique().tolist())
        marcas_top10 = st.multiselect("Filtrar por marca:", options=marcas_disp,
                                       key=f"{key_prefix}_top10_marca")
    with top10_col3:
        criterio_top10 = st.radio("Ordenar por:", ["Unidades", "Facturación"],
                                   key=f"{key_prefix}_top10_criterio", horizontal=False)

    ventas_top10 = ventas_filt.copy()
    if marcas_top10:
        ventas_top10 = ventas_top10[ventas_top10["marca"].isin(marcas_top10)]
    if busq_top10:
        bl = busq_top10.strip().lower()
        ventas_top10 = ventas_top10[
            ventas_top10["descripcion"].str.lower().str.contains(bl, na=False) |
            ventas_top10["cod_articulo"].astype(str).str.lower().str.contains(bl, na=False)
        ]

    col_orden = "cantidad" if criterio_top10 == "Unidades" else "facturacion"
    top10_art = (
        ventas_top10.groupby(["cod_articulo","descripcion"])
        .agg(cantidad=("cantidad","sum"), facturacion=("facturacion","sum"))
        .reset_index()
        .sort_values(col_orden, ascending=False)
        .head(10)
    )
    if not top10_art.empty:
        top10_art.insert(0, "#", range(1, len(top10_art)+1))
        tbl_top10 = top10_art.copy()
        tbl_top10["cantidad"]    = tbl_top10["cantidad"].apply(lambda x: f"{x:,.0f}")
        tbl_top10["facturacion"] = tbl_top10["facturacion"].apply(fmt_peso)
        tbl_top10.columns = ["#", "Código", "Descripción", "Unidades", "Facturación"]
        st.dataframe(tbl_top10, use_container_width=True, hide_index=True)
    else:
        st.info("No hay artículos para los filtros aplicados.")

    # ── Detalle mes a mes por artículo ──
    st.markdown("---")
    st.markdown("#### 🔍 Detalle por artículo (mes a mes)")
    st.caption("Buscá un artículo específico para ver su evolución mensual.")
    busq_art = st.text_input("Artículo (nombre o código):", placeholder="Ej: TORNILLO o 00345",
                              key=f"{key_prefix}_busq_art")

    if busq_art:
        busq_art_lower = busq_art.strip().lower()
        mask_art = (
            ventas_filt["descripcion"].str.lower().str.contains(busq_art_lower, na=False) |
            ventas_filt["cod_articulo"].astype(str).str.lower().str.contains(busq_art_lower, na=False)
        )
        ventas_art = ventas_filt[mask_art]

        if ventas_art.empty:
            st.warning(f"No se encontró '{busq_art}' en las compras del período con los filtros aplicados.")
        else:
            arts_encontrados = ventas_art[["cod_articulo","descripcion"]].drop_duplicates("cod_articulo")
            if len(arts_encontrados) > 1:
                opciones_art = arts_encontrados.apply(
                    lambda r: f"{r['cod_articulo']} — {r['descripcion']}", axis=1).tolist()
                sel_art = st.selectbox("Seleccioná el artículo:", opciones_art,
                                       key=f"{key_prefix}_sel_art")
                idx_art = opciones_art.index(sel_art)
                cod_art_sel = arts_encontrados.iloc[idx_art]["cod_articulo"]
                desc_art    = arts_encontrados.iloc[idx_art]["descripcion"]
                ventas_art  = ventas_art[ventas_art["cod_articulo"] == cod_art_sel]
            else:
                cod_art_sel = arts_encontrados.iloc[0]["cod_articulo"]
                desc_art    = arts_encontrados.iloc[0]["descripcion"]

            st.markdown(f"**{cod_art_sel} — {desc_art}**")

            art_mensual = ventas_art.groupby(["año","mes"]).agg(
                cantidad=("cantidad","sum"),
                facturacion=("facturacion","sum")
            ).reset_index()
            art_mensual["periodo"] = pd.to_datetime(
                art_mensual["año"].astype(str) + "-" + art_mensual["mes"].astype(str).str.zfill(2) + "-01")
            art_mensual = art_mensual.sort_values("periodo")

            ka1, ka2, ka3 = st.columns(3)
            ka1.metric("Unidades totales",  f"{art_mensual['cantidad'].sum():,.0f}")
            ka2.metric("Facturación total", fmt_peso(art_mensual["facturacion"].sum()))
            ka3.metric("Meses con compra",  len(art_mensual))

            col_art1, col_art2 = st.columns(2)
            with col_art1:
                fig_art = px.bar(art_mensual, x="periodo", y="cantidad",
                                 title="Cantidad mensual",
                                 labels={"periodo": "", "cantidad": "Cantidad"},
                                 color_discrete_sequence=["#0066cc"])
                fig_art.update_layout(xaxis_tickformat="%b %Y")
                st.plotly_chart(fig_art, use_container_width=True)
            with col_art2:
                fig_art2 = px.bar(art_mensual, x="periodo", y="facturacion",
                                  title="Facturación mensual",
                                  labels={"periodo": "", "facturacion": "Facturación ($)"},
                                  color_discrete_sequence=["#28a745"])
                fig_art2.update_layout(xaxis_tickformat="%b %Y")
                st.plotly_chart(fig_art2, use_container_width=True)

            tbl_art = art_mensual.copy()
            tbl_art["periodo"]     = tbl_art["periodo"].dt.strftime("%b %Y")
            tbl_art["cantidad"]    = tbl_art["cantidad"].apply(lambda x: f"{x:,.0f}")
            tbl_art["facturacion"] = tbl_art["facturacion"].apply(fmt_peso)
            tbl_art = tbl_art[["periodo","cantidad","facturacion"]]
            tbl_art.columns = ["Período","Cantidad","Facturación"]
            st.dataframe(tbl_art, use_container_width=True, hide_index=True)


def mapa_clientes(resumen, df_coords, color_por="estado", add_vendedor_col=None, byn=False):
    merged = resumen.merge(
        df_coords[["cod_cliente","latitud","longitud"]], on="cod_cliente", how="inner"
    )
    if merged.empty:
        return None, {}

    merged["hover_txt"] = (
        "<b>" + merged["cliente"] + "</b><br>"
        + "Última compra: "   + merged["ultima_compra"].apply(fmt_ultima) + "<br>"
        + "Días sin compra: " + merged["dias_sin_compra"].apply(fmt_dias) + "<br>"
        + "Fact. año: "       + merged["fact_año"].apply(fmt_peso) + "<br>"
        + "Estado: "          + merged["estado"]
    )
    if add_vendedor_col and add_vendedor_col in merged.columns:
        merged["hover_txt"] += "<br>Vendedor: " + merged[add_vendedor_col].fillna("-")

    # Los marcadores SIEMPRE en color (para contrastar bien en cualquier fondo)
    if color_por == "estado":
        color_map = {"ACTIVO": "#28a745", "INACTIVO": "#e74c3c", "SIN COMPRAS": "#888888"}
        kwargs = dict(color="estado", color_discrete_map=color_map)
    else:
        kwargs = dict(color=add_vendedor_col or "vendedor",
                      color_discrete_sequence=px.colors.qualitative.Set2)

    # El fondo del mapa sí cambia: B&W = fondo gris limpio, normal = callejero
    map_style = "carto-positron" if byn else "open-street-map"

    fig = px.scatter_map(
        merged, lat="latitud", lon="longitud",
        hover_name="cliente", custom_data=["hover_txt"],
        zoom=4, center=CENTRO_ARG, height=600, **kwargs,
    )
    fig.update_traces(hovertemplate="%{customdata[0]}<extra></extra>", marker_size=10)
    fig.update_layout(map_style=map_style, margin={"r":0,"t":0,"l":0,"b":0})

    # Conteos por estado
    conteos = merged["estado"].value_counts().to_dict()
    conteos["total_mapa"] = len(merged)
    conteos["total_base"] = len(resumen)
    return fig, conteos


# ── Carga automática del Excel desde GitHub ───────────────────────────────────
GDRIVE_FILE_ID   = "1w2I5XaswfouEzS7qZXnPde7smNTmP1--"
GDRIVE_COORDS_ID = "1neUDqvhShoVxtLva7YHHhWY6ocpkUKJw"

@st.cache_data(show_spinner="Cargando archivo de ventas...")
def cargar_desde_url(file_id):
    """Descarga un archivo desde Google Drive y devuelve los bytes crudos."""
    import requests, re
    errores = []
    urls_a_intentar = [
        f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t",
        f"https://drive.google.com/uc?export=download&id={file_id}",
    ]
    for url in urls_a_intentar:
        try:
            r = requests.get(url, allow_redirects=True, timeout=90)
            content_type = r.headers.get("Content-Type", "")
            # Si devuelve HTML, intentar extraer token de confirmación
            if "text/html" in content_type and r.status_code == 200:
                for pattern in [r'confirm=([0-9A-Za-z_\-]+)', r'"([0-9A-Za-z_\-]{6,})"']:
                    m = re.search(pattern, r.text)
                    if m and len(m.group(1)) > 3:
                        r2 = requests.get(
                            f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t&uuid={m.group(1)}",
                            allow_redirects=True, timeout=90
                        )
                        if r2.status_code == 200 and len(r2.content) > 1000:
                            return r2.content
            if r.status_code == 200 and len(r.content) > 1000 and b"html" not in r.content[:100].lower():
                return r.content
            errores.append(f"{url[:60]}: status={r.status_code} size={len(r.content)}")
        except Exception as e:
            errores.append(f"{url[:60]}: {e}")
    return errores  # Devolver lista de errores para mostrar al usuario

# ── Carga automática de ambos archivos ───────────────────────────────────────
archivo        = cargar_desde_url(GDRIVE_FILE_ID)
archivo_coords = cargar_desde_url(GDRIVE_COORDS_ID)

if archivo is None or isinstance(archivo, list):
    st.title("📊 Reporte General de Ventas")
    st.error("⚠️ No se pudo cargar el archivo de ventas desde Google Drive.")
    if isinstance(archivo, list):
        with st.expander("🔍 Detalle del error (para soporte técnico)"):
            for e in archivo:
                st.code(e)
    st.info("Verificá que el archivo en Google Drive esté compartido como 'Cualquier persona con el enlace'.")
    if st.button("🔄 Reintentar"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📊 Reporte General de Ventas")
    rol = st.radio("Ingresar como:", ["Vendedor","Gerencia"])

df_ventas, df_base = cargar_datos(archivo)
hoy = df_ventas["fecha"].max()
df_coords = cargar_coords(archivo_coords) if archivo_coords else None

# ── Filtros globales de clasificación (sidebar) ───────────────────────────────
with st.sidebar:
    st.markdown("---")
    st.markdown("**🔽 Filtrar por tipo de cliente:**")

    clasif_opts = sorted(df_base["clasificacion"].dropna().unique().tolist())
    sel_clasif = st.multiselect(
        "Clasificación:",
        options=clasif_opts,
        default=[],
        placeholder="Todas",
        key="filtro_clasif",
    )

    # Subclasificación dinámica según clasificación elegida
    if sel_clasif:
        base_para_sub = df_base[df_base["clasificacion"].isin(sel_clasif)]
    else:
        base_para_sub = df_base
    subclasif_opts = sorted(base_para_sub["subclasificacion"].dropna().unique().tolist())
    sel_subclasif = st.multiselect(
        "Subclasificación:",
        options=subclasif_opts,
        default=[],
        placeholder="Todas",
        key="filtro_subclasif",
    )

# Aplicar filtros a la base de clientes (afecta TODOS los análisis)
df_base_filtrada = df_base.copy()
if sel_clasif:
    df_base_filtrada = df_base_filtrada[df_base_filtrada["clasificacion"].isin(sel_clasif)]
if sel_subclasif:
    df_base_filtrada = df_base_filtrada[df_base_filtrada["subclasificacion"].isin(sel_subclasif)]

# Filtrar ventas a solo los clientes que quedaron en la base
clientes_filtrados = df_base_filtrada["cod_cliente"].unique()
df_ventas_filtrada = df_ventas[df_ventas["cod_cliente"].isin(clientes_filtrados)]

# Mostrar badge de filtros activos
filtros_activos = []
if sel_clasif:     filtros_activos.append(f"Clasif: {', '.join(sel_clasif)}")
if sel_subclasif:  filtros_activos.append(f"Subclasif: {', '.join(sel_subclasif)}")
if filtros_activos:
    st.info(f"🔽 Filtro activo: {' | '.join(filtros_activos)}")


# ══════════════════════════════════════════════════════════════════════════════
#  VISTA VENDEDOR
# ══════════════════════════════════════════════════════════════════════════════
if rol == "Vendedor":
    vdf = get_vendedores(df_base_filtrada)

    with st.sidebar:
        vendedor_sel = st.selectbox("Seleccioná tu nombre:", options=vdf["vendedor_asignado"].tolist())

    cod_sel   = vdf[vdf["vendedor_asignado"] == vendedor_sel]["cod_vendedor"].iloc[0]
    # Clientes asignados en la base (ya filtrada por clasificación)
    base_v    = df_base_filtrada[df_base_filtrada["cod_vendedor"] == cod_sel].copy()
    total_cli_base = base_v["cod_cliente"].dropna().nunique()  # Contador puro de la base

    # Ventas filtradas por cod_vendedor directamente desde la hoja Ventas completa
    # (igual que una tabla dinámica de Excel — incluye clientes que no están en la base)
    ventas_v  = df_ventas[df_ventas["cod_vendedor"] == cod_sel].copy()

    # Clientes con ventas asignadas a este vendedor pero no en su base actual
    cods_en_ventas = set(ventas_v["cod_cliente"].dropna().unique())
    cods_en_base   = set(base_v["cod_cliente"].dropna().unique())
    cods_extra     = cods_en_ventas - cods_en_base

    # Clasificar los clientes extra: reasignados (están en la base de otro) vs sin base
    reasignados_info = []
    sin_base_info    = []
    if cods_extra:
        for cod in cods_extra:
            fila_base = df_base[df_base["cod_cliente"] == cod]
            ventas_cod = ventas_v[ventas_v["cod_cliente"] == cod]
            nombre_cli = ventas_cod["cliente"].iloc[0] if not ventas_cod.empty else str(cod)
            fact_hist  = ventas_cod["facturacion"].sum()
            if not fila_base.empty:
                vend_actual = fila_base["vendedor_asignado"].iloc[0]
                reasignados_info.append({"cod_cliente": cod, "razon_social": nombre_cli,
                                         "vendedor_actual": vend_actual, "facturacion": fact_hist})
            else:
                sin_base_info.append({"cod_cliente": cod, "razon_social": nombre_cli,
                                      "vendedor_actual": "Sin registro en base", "facturacion": fact_hist})

        # Igual los agregamos a base_v para que aparezcan en el resumen
        extra_cli = (
            ventas_v[ventas_v["cod_cliente"].isin(cods_extra)]
            [["cod_cliente","cliente","localidad","provincia"]]
            .drop_duplicates("cod_cliente")
            .rename(columns={"cliente": "razon_social"})
        )
        extra_cli["cod_vendedor"]      = cod_sel
        extra_cli["vendedor_asignado"] = vendedor_sel
        base_v = pd.concat([base_v, extra_cli], ignore_index=True)

    nombre_limpio = vendedor_sel.split(")")[-1].strip() if ")" in vendedor_sel else vendedor_sel

    st.title(f"Hola, {nombre_limpio} 👋")
    st.caption(f"Datos al {hoy.strftime('%d/%m/%Y')}")

    # ── Indicador de clientes reasignados / sin base ──────────────────────────
    if reasignados_info or sin_base_info:
        total_extra     = len(reasignados_info) + len(sin_base_info)
        fact_extra      = sum(r["facturacion"] for r in reasignados_info + sin_base_info)
        with st.expander(
            f"⚠️ {total_extra} cliente{'s' if total_extra>1 else ''} con ventas históricas ya no están en tu cartera actual "
            f"— Facturación asociada: {fmt_peso(fact_extra)}"
        ):
            st.caption("Estos clientes tuvieron ventas registradas a tu nombre pero actualmente están asignados a otro vendedor o no tienen registro en la base.")
            df_extra = pd.DataFrame(reasignados_info + sin_base_info)
            df_extra["facturacion"] = df_extra["facturacion"].apply(fmt_peso)
            df_extra.columns = ["Cód. cliente","Cliente","Vendedor actual en base","Facturación histórica"]
            st.dataframe(df_extra, use_container_width=True, hide_index=True)

    # KPIs
    mes_act, año_act = hoy.month, hoy.year
    mes_ant = mes_act - 1 if mes_act > 1 else 12
    año_ant_mes = año_act if mes_act > 1 else año_act - 1

    fact_mes     = ventas_v[(ventas_v["mes"]==mes_act) & (ventas_v["año"]==año_act)]["facturacion"].sum()
    fact_mes_ant = ventas_v[(ventas_v["mes"]==mes_ant) & (ventas_v["año"]==año_ant_mes)]["facturacion"].sum()
    var_mes = (fact_mes - fact_mes_ant) / fact_mes_ant * 100 if fact_mes_ant else 0

    # Slider para umbral de actividad (días)
    dias_umbral = st.slider(
        "📅 Considerar cliente **activo** si compró en los últimos:",
        min_value=30, max_value=365, value=90, step=10,
        format="%d días", key="v_dias_umbral"
    )

    resumen = resumen_clientes(base_v, ventas_v, hoy.strftime("%Y-%m-%d"))
    # Recalcular estado según umbral elegido por el vendedor
    resumen["estado"] = resumen["dias_sin_compra"].apply(
        lambda d: "SIN COMPRAS" if d == 9999 else ("ACTIVO" if d <= dias_umbral else "INACTIVO")
    )
    activos      = (resumen["estado"] == "ACTIVO").sum()
    inactivos    = (resumen["estado"] == "INACTIVO").sum()
    sin_compras  = (resumen["estado"] == "SIN COMPRAS").sum()
    total_cli    = len(resumen)

    nombre_mes_act = MESES_FULL.get(mes_act, "")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(f"💰 Facturación {nombre_mes_act} {año_act}", fmt_peso(fact_mes), f"{var_mes:+.1f}% vs {MESES_FULL.get(mes_ant, '')}")
    c2.metric("🏢 Clientes asignados",      total_cli_base)
    c3.metric("✅ Activos",                 activos)
    c4.metric("⚠️ Inactivos",              inactivos)
    c5.metric("❌ Sin compras",            sin_compras)

    st.caption(
        f"✅ **Activo** = compró en los últimos **{dias_umbral} días**  |  "
        f"⚠️ **Inactivo** = sin compras entre {dias_umbral} y 9999 días  |  "
        f"❌ **Sin compras** = no registra ventas en el período cargado"
    )

    st.divider()

    # Tabs
    tab_labels = ["📋 Mis clientes", "⚠️ Inactivos / Sin compras", "📅 Facturación mensual", "📈 Gráficos", "🏷️ Análisis de marcas", "🔍 Mix por cliente", "📦 Artículos"]
    if df_coords is not None:
        tab_labels.append("🗺️ Mapa")
    tab_objs = st.tabs(tab_labels)
    tab_cli, tab_inact, tab_mens, tab_graf, tab_marcas_v, tab_mix_v, tab_art_v = tab_objs[:7]
    tab_map_v = tab_objs[7] if df_coords is not None else None

    with tab_cli:
        filtro = st.segmented_control(
            "Mostrar:", ["Todos","Activos","Inactivos","Sin compras"], default="Todos"
        )
        df_show = resumen.copy()
        if filtro != "Todos":
            estado_map = {"Activos":"ACTIVO","Inactivos":"INACTIVO","Sin compras":"SIN COMPRAS"}
            df_show = df_show[df_show["estado"] == estado_map[filtro]]

        df_show = df_show.sort_values("dias_sin_compra")
        df_d = df_show[["cliente","ultima_compra","dias_sin_compra","estado",
                        "fact_3m","tendencia","fact_año","marcas","localidad"]].copy()
        df_d["ultima_compra"]   = df_d["ultima_compra"].apply(fmt_ultima)
        df_d["dias_sin_compra"] = df_d["dias_sin_compra"].apply(fmt_dias)
        df_d["fact_3m"]         = df_d["fact_3m"].apply(fmt_peso)
        df_d["fact_año"]        = df_d["fact_año"].apply(fmt_peso)
        df_d["tendencia"]       = df_d["tendencia"].apply(fmt_tendencia)
        df_d.columns = ["Cliente","Última compra","Días sin compra","Estado",
                        "Fact. 3 meses","Tendencia","Fact. año","Marcas","Localidad"]
        st.dataframe(df_d, use_container_width=True, hide_index=True)

        st.markdown("---")
        with st.expander("📊 Resumen por clasificación y subclasificación"):
            resumen_clasificacion(base_v, ventas_v, resumen)

    with tab_inact:
        st.markdown("#### ¿Qué período querés analizar?")
        fecha_min = df_ventas["fecha"].min().date()
        fecha_max = df_ventas["fecha"].max().date()

        col_f1, col_f2, col_f3 = st.columns([1, 1, 2])
        with col_f1:
            desde = st.date_input("Desde", value=fecha_max.replace(day=1), min_value=fecha_min, max_value=fecha_max, key="vinact_desde")
        with col_f2:
            hasta = st.date_input("Hasta", value=fecha_max, min_value=fecha_min, max_value=fecha_max, key="vinact_hasta")
        with col_f3:
            st.caption(f"✅ Período seleccionado: **{desde.strftime('%d/%m/%Y')} → {hasta.strftime('%d/%m/%Y')}**")

        if desde > hasta:
            st.error("La fecha 'Desde' no puede ser mayor que 'Hasta'.")
            st.stop()

        # Clientes que SÍ compraron en el rango seleccionado
        ventas_rango = ventas_v[
            (ventas_v["fecha"] >= pd.Timestamp(desde)) &
            (ventas_v["fecha"] <= pd.Timestamp(hasta))
        ]
        compraron_en_rango = set(ventas_rango["cod_cliente"].unique())

        # Todos los clientes del vendedor que NO compraron en ese rango
        inact_periodo = resumen[~resumen["cod_cliente"].isin(compraron_en_rango)].copy()
        inact_periodo = inact_periodo.sort_values("dias_sin_compra", ascending=False)

        st.markdown(f"**Clientes sin compras entre {desde.strftime('%d/%m/%Y')} y {hasta.strftime('%d/%m/%Y')}:**")

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Total clientes asignados", total_cli_base)
        col_b.metric("Compraron en el período", len(compraron_en_rango))
        col_c.metric("❌ No compraron en el período", len(inact_periodo))

        if inact_periodo.empty:
            st.success("✅ ¡Todos tus clientes compraron en el período seleccionado!")
        else:
            df_i = inact_periodo[["cliente","ultima_compra","dias_sin_compra",
                                  "fact_año","marcas","localidad","provincia","telefono","mail"]].copy()
            df_i["ultima_compra"]   = df_i["ultima_compra"].apply(fmt_ultima)
            df_i["dias_sin_compra"] = df_i["dias_sin_compra"].apply(fmt_dias)
            df_i["fact_año"]        = df_i["fact_año"].apply(fmt_peso)
            df_i.columns = ["Cliente","Última compra (global)","Días sin compra",
                            "Fact. año","Marcas","Localidad","Provincia","Teléfono","Mail"]
            st.dataframe(df_i, use_container_width=True, hide_index=True)

            st.download_button(
                "📥 Descargar lista para gestión",
                df_i.to_csv(index=False).encode("utf-8"),
                file_name=f"inactivos_{nombre_limpio.replace(' ','_')}_{desde}_{hasta}.csv",
                mime="text/csv",
            )

    with tab_mens:
        st.markdown("#### Facturación mensual por año")
        st.dataframe(tabla_mensual(ventas_v), use_container_width=True)

        # ── Variación porcentual mes a mes ──
        st.markdown("#### Variación % mes a mes")
        evol_var = ventas_v.groupby(["año","mes"])["facturacion"].sum().reset_index().sort_values(["año","mes"])
        evol_var["var_pct"] = evol_var["facturacion"].pct_change() * 100
        # Resetear en cambio de año (no comparar dic año X con ene año X+1)
        for i in evol_var.index:
            if evol_var.loc[i, "mes"] == 1:
                evol_var.loc[i, "var_pct"] = None
        evol_var["Mes"] = evol_var["mes"].map(MESES_FULL)
        evol_var["Año"] = evol_var["año"].astype(str)
        evol_var["Facturación"] = evol_var["facturacion"].apply(fmt_peso)
        evol_var["Variación vs mes anterior"] = evol_var["var_pct"].apply(
            lambda v: "—" if pd.isna(v) else (f"▲ {v:.1f}%" if v >= 0 else f"▼ {abs(v):.1f}%")
        )
        st.dataframe(
            evol_var[["Año","Mes","Facturación","Variación vs mes anterior"]],
            use_container_width=True, hide_index=True
        )

        evol = ventas_v.groupby(["año","mes"])["facturacion"].sum().reset_index()
        evol["año"] = evol["año"].astype(str)
        fig = px.bar(evol, x="mes", y="facturacion", color="año", barmode="group",
                     title="Comparativa mensual por año",
                     labels={"mes":"Mes","facturacion":"Facturación ($)","año":"Año"},
                     color_discrete_sequence=px.colors.qualitative.Set1)
        fig.update_layout(xaxis=dict(tickmode="array", tickvals=list(range(1,13)), ticktext=MESES))
        st.plotly_chart(fig, use_container_width=True)

    with tab_graf:
        evol2 = ventas_v.groupby(["año","mes"])["facturacion"].sum().reset_index()
        evol2["periodo"] = pd.to_datetime(
            evol2["año"].astype(str)+"-"+evol2["mes"].astype(str).str.zfill(2)+"-01"
        )
        fig = px.bar(evol2.sort_values("periodo"), x="periodo", y="facturacion",
                     title="Facturación mensual (histórico)",
                     labels={"periodo":"","facturacion":"Facturación ($)"},
                     color_discrete_sequence=["#0066cc"])
        fig.update_layout(xaxis_tickformat="%b %Y")
        st.plotly_chart(fig, use_container_width=True)

        top10 = resumen[resumen["fact_año"]>0].nlargest(10,"fact_año")[["cliente","fact_año"]].copy()
        fig2 = px.bar(top10, x="fact_año", y="cliente", orientation="h",
                      title="Top 10 clientes — facturación año actual",
                      labels={"fact_año":"Facturación ($)","cliente":""},
                      color_discrete_sequence=["#0066cc"])
        fig2.update_layout(yaxis={"categoryorder":"total ascending"})
        st.plotly_chart(fig2, use_container_width=True)

    with tab_marcas_v:
        tab_analisis_marcas(ventas_v, base_v, key_prefix="vend")

    with tab_mix_v:
        tab_mix_cliente(ventas_v, base_v, key_prefix="vend_mix")

    with tab_art_v:
        tab_ventas_articulos(ventas_v, key_prefix="vend_art")

    if tab_map_v is not None:
        with tab_map_v:
            # ── Filtros del mapa ──
            fm1, fm2 = st.columns(2)
            with fm1:
                fmap_desde = st.date_input("Desde (actividad)", value=df_ventas["fecha"].min().date(),
                                           min_value=df_ventas["fecha"].min().date(),
                                           max_value=df_ventas["fecha"].max().date(), key="vmap_desde")
                fmap_hasta = st.date_input("Hasta (actividad)", value=df_ventas["fecha"].max().date(),
                                           min_value=df_ventas["fecha"].min().date(),
                                           max_value=df_ventas["fecha"].max().date(), key="vmap_hasta")
            with fm2:
                inact_dias_v = st.slider("Días sin compra = inactivo", 30, 365, 90, 15, key="vmap_dias")
                provs_v = sorted(resumen["provincia"].dropna().unique().tolist())
                sel_prov_v = st.multiselect("Provincia:", provs_v, default=[], placeholder="Todas", key="vmap_prov")

            locs_v = sorted(resumen[resumen["provincia"].isin(sel_prov_v) if sel_prov_v else resumen["provincia"].notna()]["localidad"].dropna().unique().tolist())
            sel_loc_v = st.multiselect("Localidad:", locs_v, default=[], placeholder="Todas", key="vmap_loc")

            # Recalcular estado según rango y umbral seleccionados
            ventas_rango_map = ventas_v[(ventas_v["fecha"] >= pd.Timestamp(fmap_desde)) & (ventas_v["fecha"] <= pd.Timestamp(fmap_hasta))]
            ultima_x_cli = ventas_rango_map.groupby("cod_cliente")["fecha"].max().reset_index()
            ultima_x_cli.columns = ["cod_cliente", "ultima_rango"]
            resumen_map = resumen.merge(ultima_x_cli, on="cod_cliente", how="left")
            ref = pd.Timestamp(fmap_hasta)
            resumen_map["dias_rango"] = resumen_map["ultima_rango"].apply(lambda x: (ref - x).days if pd.notna(x) else 9999)
            resumen_map["estado_mapa"] = resumen_map["dias_rango"].apply(
                lambda d: "ACTIVO" if d <= inact_dias_v else ("INACTIVO" if d < 9999 else "SIN COMPRAS"))
            if sel_prov_v:
                resumen_map = resumen_map[resumen_map["provincia"].isin(sel_prov_v)]
            if sel_loc_v:
                resumen_map = resumen_map[resumen_map["localidad"].isin(sel_loc_v)]

            resumen_map_tmp = resumen_map.drop(columns=["estado"]).rename(columns={"estado_mapa": "estado"})
            usar_byn_v = st.toggle("🖤 Modo blanco y negro (mayor contraste)", value=False, key="vmap_byn")
            fig_m, conteos_v = mapa_clientes(resumen_map_tmp, df_coords, color_por="estado", byn=usar_byn_v)
            if fig_m:
                n_a = conteos_v.get("ACTIVO", 0)
                n_i = conteos_v.get("INACTIVO", 0)
                n_s = conteos_v.get("SIN COMPRAS", 0)
                n_m = conteos_v.get("total_mapa", 0)
                n_b = conteos_v.get("total_base", 0)
                st.markdown(f"""
<div style="display:flex;gap:12px;margin-bottom:10px;flex-wrap:wrap">
  <div style="background:#d4f7dc;border-left:4px solid #28a745;padding:8px 20px;border-radius:6px;min-width:110px">
    <div style="font-size:11px;color:#555;font-weight:600">✅ ACTIVOS</div>
    <div style="font-size:26px;font-weight:bold;color:#28a745">{n_a}</div>
  </div>
  <div style="background:#fde8e8;border-left:4px solid #e74c3c;padding:8px 20px;border-radius:6px;min-width:110px">
    <div style="font-size:11px;color:#555;font-weight:600">⚠️ INACTIVOS</div>
    <div style="font-size:26px;font-weight:bold;color:#e74c3c">{n_i}</div>
  </div>
  <div style="background:#f0f0f0;border-left:4px solid #888;padding:8px 20px;border-radius:6px;min-width:110px">
    <div style="font-size:11px;color:#555;font-weight:600">⚫ SIN COMPRAS</div>
    <div style="font-size:26px;font-weight:bold;color:#888">{n_s}</div>
  </div>
  <div style="background:#e8f0fd;border-left:4px solid #0066cc;padding:8px 20px;border-radius:6px;min-width:130px">
    <div style="font-size:11px;color:#555;font-weight:600">📍 EN MAPA / BASE</div>
    <div style="font-size:26px;font-weight:bold;color:#0066cc">{n_m} / {n_b}</div>
  </div>
</div>
""", unsafe_allow_html=True)
                st.plotly_chart(fig_m, use_container_width=True)
            else:
                st.warning("No se encontraron coordenadas. Verificá que los códigos coincidan.")


# ══════════════════════════════════════════════════════════════════════════════
#  VISTA GERENCIA
# ══════════════════════════════════════════════════════════════════════════════
elif rol == "Gerencia":
    with st.sidebar:
        pwd = st.text_input("Contraseña:", type="password")

    if pwd != GERENCIA_PASSWORD:
        st.info("🔐 Ingresá la contraseña de gerencia en el panel izquierdo.")
        st.stop()

    # Vendedores reales
    vdf_g = get_vendedores(df_base_filtrada)
    todos_vend = vdf_g["vendedor_asignado"].tolist()

    with st.sidebar:
        st.markdown("---")
        st.markdown("**Filtrar vendedores:**")
        sel_vend = st.multiselect("Vendedores:", options=todos_vend, default=[], placeholder="Todos")

    # Filtrar base (para conteos de clientes y datos de la base)
    if sel_vend:
        cods_sel = vdf_g[vdf_g["vendedor_asignado"].isin(sel_vend)]["cod_vendedor"].tolist()
        base_g   = df_base_filtrada[df_base_filtrada["cod_vendedor"].isin(cods_sel)].copy()
    else:
        base_g = df_base_filtrada[df_base_filtrada["cod_vendedor"].isin(vdf_g["cod_vendedor"])].copy()

    # Ventas: usar df_ventas completo (igual que Excel) para que los totales coincidan
    # Si hay filtro de vendedor, filtramos por cod_vendedor en la hoja de ventas
    if sel_vend:
        cods_sel_v = [str(int(c)) for c in cods_sel if pd.notna(c)]
        ventas_g = df_ventas[
            df_ventas["cod_vendedor"].apply(
                lambda x: str(int(x)) if pd.notna(x) else ""
            ).isin(cods_sel_v)
        ].copy()
    else:
        ventas_g = df_ventas.copy()

    año_act, mes_act = hoy.year, hoy.month
    mes_ant     = mes_act - 1 if mes_act > 1 else 12
    año_ant_mes = año_act if mes_act > 1 else año_act - 1

    st.title("📊 Dashboard Gerencia")
    st.caption(
        f"{'Filtrando: ' + ', '.join(sel_vend) if sel_vend else 'Todos los vendedores'}"
        f" — Datos al {hoy.strftime('%d/%m/%Y')}"
    )

    fact_mes     = ventas_g[(ventas_g["año"]==año_act) & (ventas_g["mes"]==mes_act)]["facturacion"].sum()
    fact_mes_ant = ventas_g[(ventas_g["año"]==año_ant_mes) & (ventas_g["mes"]==mes_ant)]["facturacion"].sum()
    var_mes      = (fact_mes - fact_mes_ant) / fact_mes_ant * 100 if fact_mes_ant else 0
    fact_año     = ventas_g[ventas_g["año"]==año_act]["facturacion"].sum()

    # Cobertura de cartera: % de clientes de la base que compraron en el mes actual
    compraron_mes = set(
        ventas_g[(ventas_g["año"]==año_act) & (ventas_g["mes"]==mes_act)]["cod_cliente"].dropna().unique()
    )
    # Clientes en base: respeta el filtro de clasificación activo (df_base_filtrada)
    total_en_base     = df_base_filtrada["cod_cliente"].dropna().nunique()
    cods_base_g       = set(base_g["cod_cliente"].dropna().unique())
    compraron_en_base = len(cods_base_g & compraron_mes)
    pct_cobertura     = compraron_en_base / total_en_base * 100 if total_en_base else 0
    nombre_mes_g      = MESES_FULL.get(mes_act, "")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(f"💰 Facturación {nombre_mes_g} {año_act}", fmt_peso(fact_mes), f"{var_mes:+.1f}% vs {MESES_FULL.get(mes_ant,'')}")
    c2.metric("📅 Facturación año actual",  fmt_peso(fact_año))
    c3.metric("🏢 Clientes en base",        total_en_base)
    c4.metric("👥 Vendedores",              len(base_g["cod_vendedor"].dropna().unique()))
    c5.metric(
        f"📦 Cobertura {nombre_mes_g}",
        f"{pct_cobertura:.1f}%",
        f"{compraron_en_base} de {total_en_base} clientes compraron"
    )

    st.divider()

    tab_labels_g = ["👥 Ranking","📅 Mensual","📈 Evolución","🏷️ Marcas y rubros","🗺️ Provincia","⚠️ Inactivos por período","🔍 Análisis de marcas","🔍 Mix por cliente","📦 Artículos"]
    if df_coords is not None:
        tab_labels_g.append("🗺️ Mapa")
    tabs_g = st.tabs(tab_labels_g)
    t_rank, t_mens, t_evol, t_marc, t_prov, t_inact_g, t_marc_g, t_mix_g, t_art_g = tabs_g[:9]
    t_mapa = tabs_g[9] if df_coords is not None else None

    with t_rank:
        # Usar el vendedor de la hoja Ventas (quien realmente vendió), no la asignación de la base.
        # Así no se pierden ventas de clientes que no están en la base o están reasignados.
        excluir_vend = set(str(c) for c in EXCLUIR_CODIGOS)
        ventas_rank = ventas_g[ventas_g["cod_vendedor"].notna()].copy()
        ventas_rank = ventas_rank[~ventas_rank["cod_vendedor"].astype(str).str.replace(r'\.0$','',regex=True).isin(excluir_vend)]
        ventas_rank = ventas_rank[ventas_rank["vendedor"].notna()]

        # Si se filtró por vendedor en sidebar, respetar ese filtro
        if sel_vend:
            cods_str = set(str(int(c)) for c in vdf_g[vdf_g["vendedor_asignado"].isin(sel_vend)]["cod_vendedor"].dropna())
            ventas_rank = ventas_rank[ventas_rank["cod_vendedor"].astype(str).str.replace(r'\.0$','',regex=True).isin(cods_str)]

        ranking = (
            ventas_rank[ventas_rank["año"]==año_act]
            .groupby("vendedor")["facturacion"].sum()
            .reset_index().sort_values("facturacion", ascending=False)
        )
        col1, col2 = st.columns([2,1])
        with col1:
            fig = px.bar(ranking, x="facturacion", y="vendedor", orientation="h",
                         title=f"Facturación por vendedor — {año_act}",
                         labels={"facturacion":"Facturación ($)","vendedor":""},
                         color_discrete_sequence=["#0066cc"])
            fig.update_layout(yaxis={"categoryorder":"total ascending"}, margin=dict(t=40, r=20))
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            tbl = ranking.copy()
            tbl.insert(0, "#", range(1, len(tbl)+1))
            tbl["facturacion"] = tbl["facturacion"].apply(fmt_peso)
            tbl.columns = ["#", "Vendedor", f"Fact. {año_act}"]
            st.dataframe(tbl, use_container_width=True, hide_index=True)

        st.markdown("---")
        with st.expander("📊 Resumen por clasificación y subclasificación"):
            resumen_clasificacion(base_g, ventas_g)

    with t_mens:
        # Filtro por vendedor dentro del panel mensual
        vends_mens = ["Todos"] + sorted(ventas_rank["vendedor"].dropna().unique().tolist())
        sel_vend_mens = st.selectbox("Ver vendedor:", vends_mens, key="mens_vend_g")
        # "Todos" usa ventas_g completo (igual que Excel); vendedor específico filtra por nombre
        if sel_vend_mens == "Todos":
            ventas_mens = ventas_g
        else:
            ventas_mens = ventas_g[ventas_g["vendedor"] == sel_vend_mens]

        st.markdown(f"#### Facturación mensual — {sel_vend_mens}")
        st.dataframe(tabla_mensual(ventas_mens), use_container_width=True)
        evol_g = ventas_mens.groupby(["año","mes"])["facturacion"].sum().reset_index()
        evol_g["año"] = evol_g["año"].astype(str)
        fig = px.bar(evol_g, x="mes", y="facturacion", color="año", barmode="group",
                     title=f"Comparativa mensual por año — {sel_vend_mens}",
                     labels={"mes":"Mes","facturacion":"Facturación ($)","año":"Año"},
                     color_discrete_sequence=px.colors.qualitative.Set1)
        fig.update_layout(xaxis=dict(tickmode="array", tickvals=list(range(1,13)), ticktext=MESES))
        st.plotly_chart(fig, use_container_width=True)

    with t_evol:
        evol_t = ventas_g.groupby(["año","mes"])["facturacion"].sum().reset_index()
        evol_t["periodo"] = pd.to_datetime(
            evol_t["año"].astype(str)+"-"+evol_t["mes"].astype(str).str.zfill(2)+"-01"
        )
        fig = px.line(evol_t.sort_values("periodo"), x="periodo", y="facturacion",
                      title="Evolución mensual total",
                      labels={"periodo":"","facturacion":"Facturación ($)"},
                      markers=True, color_discrete_sequence=["#0066cc"])
        fig.update_layout(xaxis_tickformat="%b %Y", hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

        evol_v = (
            ventas_rank[ventas_rank["año"]==año_act]
            .groupby(["mes","vendedor"])["facturacion"].sum().reset_index()
        )
        fig2 = px.bar(evol_v, x="mes", y="facturacion", color="vendedor",
                      title=f"Facturación mensual por vendedor — {año_act}",
                      labels={"mes":"Mes","facturacion":"Facturación ($)","vendedor":"Vendedor"})
        fig2.update_layout(xaxis=dict(tickmode="array", tickvals=list(range(1,13)), ticktext=MESES))
        st.plotly_chart(fig2, use_container_width=True)

    with t_marc:
        col1, col2 = st.columns(2)
        with col1:
            marcas = (
                ventas_g[ventas_g["año"]==año_act]
                .groupby("marca")["facturacion"].sum().reset_index()
                .dropna(subset=["marca"]).sort_values("facturacion", ascending=False).head(15)
            )
            fig = px.bar(marcas, x="facturacion", y="marca", orientation="h",
                         title=f"Top marcas — {año_act}",
                         labels={"facturacion":"Facturación ($)","marca":""},
                         color_discrete_sequence=["#0066cc"])
            fig.update_layout(yaxis={"categoryorder":"total ascending"})
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            rubros = (
                ventas_g[ventas_g["año"]==año_act]
                .groupby("rubro")["facturacion"].sum().reset_index()
                .dropna(subset=["rubro"]).sort_values("facturacion", ascending=False).head(12)
            )
            fig = px.pie(rubros, values="facturacion", names="rubro",
                         title=f"Facturación por rubro — {año_act}")
            st.plotly_chart(fig, use_container_width=True)

    with t_prov:
        prov = (
            ventas_g[ventas_g["año"]==año_act]
            .groupby("provincia")["facturacion"].sum().reset_index()
            .dropna(subset=["provincia"]).sort_values("facturacion", ascending=False)
        )
        fig = px.bar(prov, x="provincia", y="facturacion",
                     title=f"Facturación por provincia — {año_act}",
                     labels={"provincia":"Provincia","facturacion":"Facturación ($)"},
                     color_discrete_sequence=["#0066cc"])
        fig.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)

    with t_inact_g:
        st.markdown("#### ¿Qué período querés analizar?")
        fecha_min_g = df_ventas["fecha"].min().date()
        fecha_max_g = df_ventas["fecha"].max().date()

        col_f1, col_f2, _ = st.columns([1, 1, 2])
        with col_f1:
            desde_g = st.date_input("Desde", value=fecha_max_g.replace(day=1),
                                    min_value=fecha_min_g, max_value=fecha_max_g, key="desde_g")
        with col_f2:
            hasta_g = st.date_input("Hasta", value=fecha_max_g,
                                    min_value=fecha_min_g, max_value=fecha_max_g, key="hasta_g")

        if desde_g > hasta_g:
            st.error("La fecha 'Desde' no puede ser mayor que 'Hasta'.")
        else:
            ventas_rango_g = ventas_g[
                (ventas_g["fecha"] >= pd.Timestamp(desde_g)) &
                (ventas_g["fecha"] <= pd.Timestamp(hasta_g))
            ]
            compraron_g = set(ventas_rango_g["cod_cliente"].unique())

            # Resumen de todos los clientes de la base filtrada
            resumen_inact_g = resumen_clientes(base_g, ventas_g, hoy.strftime("%Y-%m-%d"))
            resumen_inact_g = resumen_inact_g.merge(
                base_g[["cod_cliente","vendedor_asignado"]].drop_duplicates("cod_cliente"),
                on="cod_cliente", how="left"
            )
            inact_g = resumen_inact_g[~resumen_inact_g["cod_cliente"].isin(compraron_g)].copy()
            inact_g = inact_g.sort_values("dias_sin_compra", ascending=False)

            st.markdown(f"**Sin compras entre {desde_g.strftime('%d/%m/%Y')} y {hasta_g.strftime('%d/%m/%Y')}:**")

            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Total clientes en base", len(base_g))
            col_b.metric("Compraron en el período", len(compraron_g))
            col_c.metric("❌ No compraron en el período", len(inact_g))

            # Agrupar por vendedor
            if not inact_g.empty:
                por_vendedor = (
                    inact_g.groupby("vendedor_asignado")
                    .size().reset_index(name="sin_compras")
                    .sort_values("sin_compras", ascending=False)
                )
                fig_ig = px.bar(
                    por_vendedor, x="sin_compras", y="vendedor_asignado", orientation="h",
                    title="Clientes inactivos en el período por vendedor",
                    labels={"sin_compras": "Clientes sin compra", "vendedor_asignado": ""},
                    color_discrete_sequence=["#e74c3c"],
                )
                fig_ig.update_layout(yaxis={"categoryorder": "total ascending"})
                st.plotly_chart(fig_ig, use_container_width=True)

                # Tabla detalle con filtro por vendedor
                vend_opts = ["Todos"] + sorted(inact_g["vendedor_asignado"].dropna().unique().tolist())
                vend_filtro = st.selectbox("Ver detalle por vendedor:", vend_opts)
                if vend_filtro != "Todos":
                    inact_g = inact_g[inact_g["vendedor_asignado"] == vend_filtro]

                df_ig = inact_g[["cliente","vendedor_asignado","ultima_compra","dias_sin_compra",
                                  "fact_año","marcas","localidad","provincia"]].copy()
                df_ig["ultima_compra"]   = df_ig["ultima_compra"].apply(fmt_ultima)
                df_ig["dias_sin_compra"] = df_ig["dias_sin_compra"].apply(fmt_dias)
                df_ig["fact_año"]        = df_ig["fact_año"].apply(fmt_peso)
                df_ig.columns = ["Cliente","Vendedor","Última compra (global)","Días sin compra",
                                  "Fact. año","Marcas","Localidad","Provincia"]
                st.dataframe(df_ig, use_container_width=True, hide_index=True)

                st.download_button(
                    "📥 Descargar lista completa",
                    df_ig.to_csv(index=False).encode("utf-8"),
                    file_name=f"inactivos_gerencia_{desde_g}_{hasta_g}.csv",
                    mime="text/csv",
                )

    with t_marc_g:
        tab_analisis_marcas(ventas_g, base_g, key_prefix="ger", vendedores_disponibles=todos_vend)

    with t_mix_g:
        tab_mix_cliente(ventas_g, base_g, key_prefix="ger_mix")

    with t_art_g:
        tab_ventas_articulos(ventas_g, key_prefix="ger_art")

    if t_mapa is not None:
        with t_mapa:
            gm1, gm2 = st.columns(2)
            with gm1:
                gmap_desde = st.date_input("Desde (actividad)", value=df_ventas["fecha"].min().date(),
                                           min_value=df_ventas["fecha"].min().date(),
                                           max_value=df_ventas["fecha"].max().date(), key="gmap_desde")
                gmap_hasta = st.date_input("Hasta (actividad)", value=df_ventas["fecha"].max().date(),
                                           min_value=df_ventas["fecha"].min().date(),
                                           max_value=df_ventas["fecha"].max().date(), key="gmap_hasta")
                inact_dias_g = st.slider("Días sin compra = inactivo", 30, 365, 90, 15, key="gmap_dias")
            with gm2:
                col_opt_g = st.radio("Colorear por:", ["Estado (activo/inactivo)", "Vendedor"], key="gmap_color")
                vends_mapa = sorted(base_g["vendedor_asignado"].dropna().unique().tolist())
                sel_vend_mapa = st.multiselect("Vendedores en mapa:", vends_mapa, default=[], placeholder="Todos", key="gmap_vend")

            resumen_g_full = resumen_clientes(base_g, ventas_g, hoy.strftime("%Y-%m-%d"))
            resumen_g_full = resumen_g_full.merge(
                base_g[["cod_cliente","vendedor_asignado"]].drop_duplicates("cod_cliente"), on="cod_cliente", how="left")

            provs_g = sorted(resumen_g_full["provincia"].dropna().unique().tolist())
            sel_prov_g = st.multiselect("Provincia:", provs_g, default=[], placeholder="Todas", key="gmap_prov")
            locs_base_g = resumen_g_full[resumen_g_full["provincia"].isin(sel_prov_g)] if sel_prov_g else resumen_g_full
            locs_g = sorted(locs_base_g["localidad"].dropna().unique().tolist())
            sel_loc_g = st.multiselect("Localidad:", locs_g, default=[], placeholder="Todas", key="gmap_loc")

            # Recalcular estado según rango y umbral
            ventas_rango_g_map = ventas_g[
                (ventas_g["fecha"] >= pd.Timestamp(gmap_desde)) &
                (ventas_g["fecha"] <= pd.Timestamp(gmap_hasta))
            ]
            ultima_g = ventas_rango_g_map.groupby("cod_cliente")["fecha"].max().reset_index()
            ultima_g.columns = ["cod_cliente","ultima_rango"]
            resumen_g_map = resumen_g_full.merge(ultima_g, on="cod_cliente", how="left")
            ref_g = pd.Timestamp(gmap_hasta)
            resumen_g_map["dias_rango"] = resumen_g_map["ultima_rango"].apply(
                lambda x: (ref_g - x).days if pd.notna(x) else 9999)
            resumen_g_map["estado"] = resumen_g_map["dias_rango"].apply(
                lambda d: "ACTIVO" if d <= inact_dias_g else ("INACTIVO" if d < 9999 else "SIN COMPRAS"))

            if sel_vend_mapa:
                resumen_g_map = resumen_g_map[resumen_g_map["vendedor_asignado"].isin(sel_vend_mapa)]
            if sel_prov_g:
                resumen_g_map = resumen_g_map[resumen_g_map["provincia"].isin(sel_prov_g)]
            if sel_loc_g:
                resumen_g_map = resumen_g_map[resumen_g_map["localidad"].isin(sel_loc_g)]

            color_por_g = "estado" if "Estado" in col_opt_g else "vendedor"
            usar_byn_g = st.toggle("🖤 Modo blanco y negro (mayor contraste)", value=False, key="gmap_byn")
            fig_m, conteos_g = mapa_clientes(resumen_g_map, df_coords, color_por=color_por_g,
                                             add_vendedor_col="vendedor_asignado", byn=usar_byn_g)
            if fig_m:
                n_a = conteos_g.get("ACTIVO", 0)
                n_i = conteos_g.get("INACTIVO", 0)
                n_s = conteos_g.get("SIN COMPRAS", 0)
                n_m = conteos_g.get("total_mapa", 0)
                n_b = conteos_g.get("total_base", 0)
                st.markdown(f"""
<div style="display:flex;gap:12px;margin-bottom:10px;flex-wrap:wrap">
  <div style="background:#d4f7dc;border-left:4px solid #28a745;padding:8px 20px;border-radius:6px;min-width:110px">
    <div style="font-size:11px;color:#555;font-weight:600">✅ ACTIVOS</div>
    <div style="font-size:26px;font-weight:bold;color:#28a745">{n_a}</div>
  </div>
  <div style="background:#fde8e8;border-left:4px solid #e74c3c;padding:8px 20px;border-radius:6px;min-width:110px">
    <div style="font-size:11px;color:#555;font-weight:600">⚠️ INACTIVOS</div>
    <div style="font-size:26px;font-weight:bold;color:#e74c3c">{n_i}</div>
  </div>
  <div style="background:#f0f0f0;border-left:4px solid #888;padding:8px 20px;border-radius:6px;min-width:110px">
    <div style="font-size:11px;color:#555;font-weight:600">⚫ SIN COMPRAS</div>
    <div style="font-size:26px;font-weight:bold;color:#888">{n_s}</div>
  </div>
  <div style="background:#e8f0fd;border-left:4px solid #0066cc;padding:8px 20px;border-radius:6px;min-width:130px">
    <div style="font-size:11px;color:#555;font-weight:600">📍 EN MAPA / BASE</div>
    <div style="font-size:26px;font-weight:bold;color:#0066cc">{n_m} / {n_b}</div>
  </div>
</div>
""", unsafe_allow_html=True)
                st.plotly_chart(fig_m, use_container_width=True)
            else:
                st.warning("No se encontraron coordenadas. Verificá que los códigos de cliente coincidan.")
