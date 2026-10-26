import { useEffect, useRef, useState } from "react";
import ChatWidget from "./ChatWidget";

type Metadata = {
  min_date: string | null;
  latest_date: string | null;
  stores: { store_id: number; store_name: string }[];
};
type TableColumn = { column_name: string; column_type: string; is_nullable: boolean; key: string | null };
type DatabaseTable = { table_name: string; columns: TableColumn[] };
type TablePreview = { table_name: string; columns: string[]; rows: unknown[][]; limit: number; offset: number };
type SyncStatus = { status: "idle" | "running" | "succeeded" | "failed"; pid: number | null; started_at: string | null; finished_at: string | null; return_code: number | null; updated_tables?: string[] };

type SkuRow = {
  sku_id: number;
  sku_name: string;
  store_id: number | null;
  store_name: string;
  total_quantity: number;
  total_sales_value: number;
};

type TrendPoint = {
  sku_id: number;
  sku_name: string;
  store_id: number | null;
  store_name: string;
  value: number;
};

type StoreMetric = "item_sales_value" | "total_quantity";
type StoreRow = {
  store_id: number;
  store_name: string;
  total_orders: number;
  total_customers: number | null;
  total_order_value: number;
  total_quantity: number;
  item_sales_value: number;
  average_order_value: number;
};
type StoreReport = { start_date: string; end_date: string; stores: StoreRow[] };
type InventoryPoint = { sku_id: number; sku_name: string; store_id: number | null; store_name: string; stock_quantity: number; total_quantity: number; total_sales_value: number; report_date: string };
type InventoryReport = { start_date: string; end_date: string; points: InventoryPoint[] };
type FoodTypeRow = { food_type_name: string; total_quantity_sold: number; total_sales_value: number; total_orders: number };
type FoodTypeReport = { start_date: string; end_date: string; food_types: FoodTypeRow[] };
type HourlySalesReport = {
  start_date: string;
  end_date: string;
  hourly_sales: { hour: number; total_sales_value: number; total_orders: number }[];
  unassigned: { rows: number; total_orders: number; total_sales_value: number };
};
type StoreHourlyPoint = { store_id: number; store_name: string; hour: number; total_sales_value: number; total_orders: number; total_quantity: number };
type StoreHourlyReport = { start_date: string; end_date: string; rows: StoreHourlyPoint[] };
type CustomerRecord = {
  customer_id: number | string;
  customer_name: string;
  total_orders: number;
  total_quantity: number;
  total_sales: number;
  last_order_date: string;
};
type CustomerReport = {
  start_date: string;
  end_date: string;
  top_by_orders: CustomerRecord[];
  top_by_sales: CustomerRecord[];
};
type CustomerSegment = { customer_segment: string; customer_count: number; total_orders: number; total_quantity: number; total_sales: number };
type CustomerSegmentsReport = { start_date: string; end_date: string; segments: CustomerSegment[] };
type HighInventoryItem = {
  report_date: string;
  sku_id: number;
  store_id: number;
  store_name: string;
  sku_name: string;
  stock_quantity: number;
  low_quantity_alert: number;
  inventory_value: number;
  sold_quantity_30day: number;
  sales_value_30day: number;
};
type HighInventoryReport = { start_date: string; end_date: string; candidate_count: number; items: HighInventoryItem[] };
type InventoryAlertRow = {
  sku_id: number;
  store_id: number;
  snapshot_date: string;
  sku_name: string;
  store_name: string;
  inventory_quantity: number;
  inventory_value: number;
  units_sold_30d: number;
  sales_amount_30d: number;
  max_units_sold?: number;
  min_inventory_quantity?: number;
  min_units_sold?: number;
  max_inventory_quantity?: number;
};
type InventoryAlertPage = { total: number; limit: number; offset: number; rows: InventoryAlertRow[] };
type InventoryAlertsReport = {
  start_date: string;
  end_date: string;
  low_sales_high_inventory: InventoryAlertPage;
  high_sales_low_inventory: InventoryAlertPage;
};
type StoreFeedbackReview = {
  feedback_id: number;
  order_id: number | null;
  store_id: number;
  store_name: string;
  customer_id: number | null;
  customer_name: string;
  rating: number;
  description: string;
  feedback_created_at: string;
};
type StoreFeedbackReport = {
  summary: { total: number; average_rating: number; rating_counts: Record<string, number> };
  stores: { store_id: number; store_name: string; review_count: number; average_rating: number }[];
  total: number;
  limit: number;
  offset: number;
  reviews: StoreFeedbackReview[];
};
type CustomerJourneyReport = {
  scope: "all_stores" | "selected_stores";
  store_name: string;
  store_count: number;
  refreshed_at: string | null;
  stages: { stage: "Cart" | "Order" | "Payment"; customer_count: number }[];
};

type Report = {
  start_date: string;
  end_date: string;
  total_skus: number;
  total_quantity: number;
  total_orders: number;
  total_sales_value: number;
  trend: { sku_id: number; sku_name: string; store_id: number | null; store_name: string; sales_value: number }[];
  quantity_trend: { sku_id: number; sku_name: string; store_id: number | null; store_name: string; quantity: number }[];
  quantity_records: { rank: number; sku_id: number; sku_name: string; quantity: number }[];
  value_records: { rank: number; sku_id: number; sku_name: string; sales_value: number }[];
  rows: SkuRow[];
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

function dateString(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function fromDateString(value: string): Date {
  return new Date(`${value}T00:00:00Z`);
}

function lastSevenDays(latestDate: string): [string, string] {
  const end = fromDateString(latestDate);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 6);
  return [dateString(start), latestDate];
}

function lastCalendarMonth(anchorDate: string): [string, string] {
  const anchor = fromDateString(anchorDate);
  const firstOfCurrentMonth = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth(), 1));
  const lastOfPreviousMonth = new Date(firstOfCurrentMonth);
  lastOfPreviousMonth.setUTCDate(0);
  const firstOfPreviousMonth = new Date(
    Date.UTC(lastOfPreviousMonth.getUTCFullYear(), lastOfPreviousMonth.getUTCMonth(), 1),
  );
  return [dateString(firstOfPreviousMonth), dateString(lastOfPreviousMonth)];
}

function prettyDate(value: string | null): string {
  if (!value) return "—";
  return fromDateString(value).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

function number(value: number, digits = 0): string {
  return new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  }).format(value ?? 0);
}

function money(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value ?? 0);
}

function compactMoney(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value ?? 0);
}

function SkuLineChart({ points, metric }: { points: TrendPoint[]; metric: "quantity" | "value" }) {
  const width = 900;
  const height = 500;
  const left = 74;
  const right = 28;
  const top = 24;
  const bottom = 60;
  const max = Math.max(1, ...points.map((point) => point.value));
  const skuIds = [...new Set(points.map((point) => point.sku_id))];
  const coords = points.map((point, index) => ({
    ...point,
    x: left + (skuIds.length < 2 ? 0 : (skuIds.indexOf(point.sku_id) / (skuIds.length - 1)) * (width - left - right)),
    y: top + (1 - point.value / max) * (height - top - bottom),
  }));
  const path = coords.reduce((result, point, index) => {
    if (index === 0) return `M${point.x},${point.y}`;
    const previous = coords[index - 1];
    const controlX = (previous.x + point.x) / 2;
    const controlY = (previous.y + point.y) / 2 + (previous.y - point.y) * 0.08;
    return `${result} Q${controlX},${controlY} ${point.x},${point.y}`;
  }, "");
  const baseline = height - bottom;
  const areaPath = coords.length ? `${path} L${coords[coords.length - 1].x},${baseline} L${coords[0].x},${baseline} Z` : "";
  const labelIds = skuIds;

  if (!points.length) return <div className="chart-empty">No sales found for this range.</div>;

  return (
    <div className="line-chart-wrap">
      <svg className={`line-chart line-chart-${metric}`} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${metric === "value" ? "Sales value" : "Quantity sold"} by SKU ID line chart`}>
        <defs><linearGradient id={`quantity-area-${metric}`} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#27a979" stopOpacity=".14" /><stop offset="100%" stopColor="#27a979" stopOpacity=".015" /></linearGradient></defs>
        {[0, 0.5, 1].map((fraction) => {
          const y = top + fraction * (height - top - bottom);
          return <g key={fraction}><line x1={left} x2={width - right} y1={y} y2={y} className="chart-gridline" /><text x={left - 9} y={y + 4} textAnchor="end" className="chart-axis-label">{metric === "value" ? compactMoney(max * (1 - fraction)) : number(max * (1 - fraction))}</text></g>;
        })}
        {areaPath ? <path d={areaPath} className="chart-area" /> : null}
        {path ? <path d={path} pathLength={1} className="chart-line" /> : null}
        {coords.map((point, index) => {
          const tooltipX = Math.min(Math.max(point.x - 150, left + 8), width - right - 316);
          const tooltipY = Math.max(4, point.y - 142);
          return <g key={`${point.sku_id}-${point.store_id}-${index}`} className="chart-point-group"><circle cx={point.x} cy={point.y} r="10" className="chart-point-halo" /><circle cx={point.x} cy={point.y} r="4.5" className="chart-point" /><foreignObject x={tooltipX} y={tooltipY} width="308" height="132" className="chart-tooltip-object"><div className="chart-tooltip"><strong>{point.sku_name}</strong><span>SKU {point.sku_id} · {point.store_name}</span><b>{metric === "value" ? money(point.value) : number(point.value)} <small>{metric === "value" ? "sales value" : "units sold"}</small></b></div></foreignObject></g>;
        })}
        {labelIds.map((skuId) => <text key={skuId} x={left + (skuIds.length < 2 ? 0 : (skuIds.indexOf(skuId) / (skuIds.length - 1)) * (width - left - right))} y={height - 26} textAnchor="middle" className="chart-axis-label">{skuId}</text>)}
        <text x={width / 2} y={height - 4} textAnchor="middle" className="chart-axis-title">SKU ID</text>
        <text x="18" y={height / 2} textAnchor="middle" className="chart-axis-title" transform={`rotate(-90 18 ${height / 2})`}>{metric === "value" ? "Total sales value (INR)" : "Total quantity sold"}</text>
      </svg>
    </div>
  );
}

async function getReport(start: string, end: string, chartLimit: number, storeIds: string[]): Promise<Report> {
  const params = new URLSearchParams({ start_date: start, end_date: end, chart_limit: String(chartLimit) });
  storeIds.forEach((storeId) => params.append("store_id", storeId));
  const response = await fetch(`${API_BASE}/api/reports/top-selling-skus-count?${params}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Could not load the report.");
  }
  return response.json();
}

async function getStoreReport(start: string, end: string, storeIds: string[]): Promise<StoreReport> {
  const params = new URLSearchParams({ start_date: start, end_date: end });
  storeIds.forEach((storeId) => params.append("store_id", storeId));
  const response = await fetch(`${API_BASE}/api/reports/store-performance?${params}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Could not load store performance.");
  }
  return response.json();
}

async function getInventoryReport(start: string, end: string, storeIds: string[]): Promise<InventoryReport> {
  const params = new URLSearchParams({ start_date: start, end_date: end });
  storeIds.forEach((storeId) => params.append("store_id", storeId));
  const response = await fetch(`${API_BASE}/api/reports/inventory-demand?${params}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Could not load inventory demand.");
  }
  return response.json();
}

async function getFoodTypeReport(start: string, end: string, storeIds: string[]): Promise<FoodTypeReport> {
  const params = new URLSearchParams({ start_date: start, end_date: end });
  storeIds.forEach((storeId) => params.append("store_id", storeId));
  const response = await fetch(`${API_BASE}/api/reports/food-type-sales?${params}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Could not load food type sales.");
  }
  return response.json();
}

async function getHourlySalesReport(start: string, end: string, storeIds: string[]): Promise<HourlySalesReport> {
  const params = new URLSearchParams({ start_date: start, end_date: end });
  storeIds.forEach((storeId) => params.append("store_id", storeId));
  const response = await fetch(`${API_BASE}/api/reports/hourly-store-sales?${params}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Could not load hourly sales.");
  }
  return response.json();
}

async function getStoreHourlyReport(start: string, end: string, storeIds: string[]): Promise<StoreHourlyReport> {
  const params = new URLSearchParams({ start_date: start, end_date: end });
  storeIds.forEach((storeId) => params.append("store_id", storeId));
  const response = await fetch(`${API_BASE}/api/reports/store-hourly-sales?${params}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Could not load store hourly sales.");
  }
  return response.json();
}

async function getTopCustomersReport(start: string, end: string, limit: number, storeIds: string[]): Promise<CustomerReport> {
  const params = new URLSearchParams({ start_date: start, end_date: end, chart_limit: String(limit) });
  storeIds.forEach((storeId) => params.append("store_id", storeId));
  const response = await fetch(`${API_BASE}/api/reports/top-customers?${params}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Could not load top customer data.");
  }
  return response.json();
}

async function getCustomerSegmentsReport(start: string, end: string, storeIds: string[]): Promise<CustomerSegmentsReport> {
  const params = new URLSearchParams({ start_date: start, end_date: end });
  storeIds.forEach((storeId) => params.append("store_id", storeId));
  const response = await fetch(`${API_BASE}/api/reports/repeat-nonrepeated-customers?${params}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Could not load repeat customer segments.");
  }
  return response.json();
}

async function getHighInventoryReport(start: string, end: string, storeIds: string[]): Promise<HighInventoryReport> {
  const params = new URLSearchParams({ start_date: start, end_date: end });
  storeIds.forEach((storeId) => params.append("store_id", storeId));
  const response = await fetch(`${API_BASE}/api/reports/high-inventory-low-sales?${params}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Could not load high-inventory items.");
  }
  return response.json();
}

async function getInventoryAlertsReport(start: string, end: string, storeIds: string[], offset = 0): Promise<InventoryAlertsReport> {
  const params = new URLSearchParams({ start_date: start, end_date: end, limit: "100", offset: String(offset) });
  storeIds.forEach((storeId) => params.append("store_id", storeId));
  const response = await fetch(`${API_BASE}/api/reports/inventory-alerts?${params}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Could not load inventory alert tables.");
  }
  return response.json();
}

function HighInventoryChart({ report, storeIds }: { report: HighInventoryReport | null; storeIds: string[] }) {
  const items = report?.items ?? [];
  const max = Math.max(1, ...items.map((item) => item.stock_quantity));
  const width = 1000;
  const left = 390;
  const right = 72;
  const top = 36;
  const rowHeight = 43;
  const height = top + items.length * rowHeight + 18;
  const plotWidth = width - left - right;

  return <section className="panel high-inventory-panel">
    <div className="panel-heading high-inventory-heading">
      <div><span className="panel-kicker">INVENTORY ALERT</span><h2>High inventory, low sales</h2><p>Products flagged by the MySQL ETL for high inventory and low sales in its 30-day window.</p></div>
      <span className="row-count">{number(report?.candidate_count ?? 0)} SKU / store pairs</span>
    </div>
    {items.length ? <>
      <div className="high-inventory-chart-wrap"><svg className="high-inventory-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Ranked horizontal bar chart of stock quantities for high-inventory low-sales SKU and store pairs">
        {items.map((item, index) => {
          const y = top + index * rowHeight;
          const barWidth = item.stock_quantity / max * plotWidth;
          const alertX = left + item.low_quantity_alert / max * plotWidth;
          return <g key={`${item.sku_id}-${item.store_id}`} className="high-inventory-row">
            <text x={left - 16} y={y + 10} textAnchor="end" className="high-inventory-name">{item.sku_name.length > 39 ? `${item.sku_name.slice(0, 38)}…` : item.sku_name}<title>{item.sku_name}</title></text>
            <text x={left - 16} y={y + 27} textAnchor="end" className="high-inventory-store">{item.store_name} · SKU {item.sku_id}</text>
            <rect x={left} y={y - 1} width={plotWidth} height="23" rx="6" className="high-inventory-track" />
            <rect x={left} y={y - 1} width={barWidth} height="23" rx="6" className="high-inventory-bar"><title>{item.sku_name} · {item.store_name}: {number(item.stock_quantity)} in stock, minimum stock {number(item.low_quantity_alert)}, 30-day sales {money(item.sales_value_30day)}</title></rect>
            <line x1={alertX} x2={alertX} y1={y - 5} y2={y + 27} className="high-inventory-alert-marker"><title>Low-stock alert: {number(item.low_quantity_alert)}</title></line>
            <text x={left + barWidth + 8} y={y + 15} className="high-inventory-value">{number(item.stock_quantity)}</text>
          </g>;
        })}
      </svg></div>
      <div className="high-inventory-legend"><span><i className="high-inventory-legend-stock" />Stock quantity</span><span><i className="high-inventory-legend-alert" />Low-stock alert</span></div>
      {items.every((item) => item.sold_quantity_30day === 0) ? <div className="high-inventory-note">The selected records currently all show zero sales in the 30-day window.</div> : null}
    </> : <div className="chart-empty">No high-inventory, low-sales items found for this selection.</div>}
    <div className="table-footer">Showing up to 12 SKU / store pairs from the low_sales_high_inventory MySQL table.</div>
  </section>;
}

function InventoryAlertTables({ report, loading, offset, onOffsetChange }: {
  report: InventoryAlertsReport | null;
  loading: boolean;
  offset: number;
  onOffsetChange: (offset: number) => void;
}) {
  const [alertType, setAlertType] = useState<"low_sales_high_inventory" | "high_sales_low_inventory">("low_sales_high_inventory");
  const page = report?.[alertType];
  const lowSalesHighInventory = alertType === "low_sales_high_inventory";
  return <section className="panel inventory-alert-table-panel">
    <div className="panel-heading inventory-alert-table-heading">
      <div><span className="panel-kicker">INVENTORY ALERTS</span><h2>{lowSalesHighInventory ? "Low Sales, High Inventory" : "High Sales, Low Inventory"}</h2></div>
      <span className="row-count">{number(page?.total ?? 0)} SKU / store rows</span>
    </div>
    <div className="inventory-alert-tabs" role="tablist" aria-label="Inventory alert type">
      <button type="button" role="tab" aria-selected={lowSalesHighInventory} className={lowSalesHighInventory ? "inventory-alert-tab-active" : ""} onClick={() => { setAlertType("low_sales_high_inventory"); if (offset) onOffsetChange(0); }}>Low sales, high inventory</button>
      <button type="button" role="tab" aria-selected={!lowSalesHighInventory} className={!lowSalesHighInventory ? "inventory-alert-tab-active" : ""} onClick={() => { setAlertType("high_sales_low_inventory"); if (offset) onOffsetChange(0); }}>High sales, low inventory</button>
    </div>
    <p className="inventory-alert-help">Warning-colored cells identify the inventory or sales value that triggered the alert.</p>
    <div className="table-scroll inventory-alert-table-scroll">
      <table className="inventory-alert-table"><thead><tr>
        <th>Snapshot</th><th>SKU</th><th>Product</th><th>Store</th><th>Stock</th><th>30-day units sold</th><th>Inventory value</th><th>30-day sales</th>
      </tr></thead><tbody>
        {(page?.rows ?? []).map((row) => {
          const inventoryWarning = lowSalesHighInventory
            ? row.min_inventory_quantity != null && row.inventory_quantity >= row.min_inventory_quantity
            : row.max_inventory_quantity != null && row.inventory_quantity <= row.max_inventory_quantity;
          const salesWarning = lowSalesHighInventory
            ? row.max_units_sold != null && row.units_sold_30d <= row.max_units_sold
            : row.min_units_sold != null && row.units_sold_30d >= row.min_units_sold;
          return <tr key={`${alertType}-${row.sku_id}-${row.store_id}-${row.snapshot_date}`}>
            <td>{prettyDate(row.snapshot_date)}</td><td>{row.sku_id}</td><td>{row.sku_name}</td><td>{row.store_name}</td>
            <td className={inventoryWarning ? "inventory-alert-cell inventory-alert-stock" : ""}>{number(row.inventory_quantity)}</td>
            <td className={salesWarning ? "inventory-alert-cell inventory-alert-sales" : ""}>{number(row.units_sold_30d)}</td>
            <td>{money(row.inventory_value)}</td><td>{money(row.sales_amount_30d)}</td>
          </tr>;
        })}
      </tbody></table>
      {loading ? <div className="chart-empty">Loading inventory alerts…</div> : null}
      {!loading && !page?.rows.length ? <div className="chart-empty">No rows match the selected date and store filters.</div> : null}
    </div>
    <div className="inventory-alert-pagination"><span>Showing {page?.rows.length ? `${offset + 1}–${offset + page.rows.length}` : "0"} of {number(page?.total ?? 0)}</span><div>
      <button type="button" disabled={loading || offset === 0} onClick={() => onOffsetChange(Math.max(0, offset - 100))}>Previous</button>
      <button type="button" disabled={loading || offset + 100 >= (page?.total ?? 0)} onClick={() => onOffsetChange(offset + 100)}>Next</button>
    </div></div>
  </section>;
}

function StoreFeedbackReviews({ refreshKey, stores }: {
  refreshKey: number;
  stores: Metadata["stores"];
}) {
  const [report, setReport] = useState<StoreFeedbackReport | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      const load = async () => {
        setLoading(true);
        setError("");
        try {
          const params = new URLSearchParams({ limit: "100", offset: String(offset) });
          const response = await fetch(`${API_BASE}/api/reports/store-feedback?${params}`, { cache: "no-store" });
          if (!response.ok) {
            const body = await response.json().catch(() => null);
            throw new Error(body?.detail ?? "Could not load store reviews.");
          }
          const data = await response.json() as StoreFeedbackReport;
          if (active) setReport(data);
        } catch (err) {
          if (active) setError(err instanceof Error ? err.message : "Could not load store reviews.");
        } finally {
          if (active) setLoading(false);
        }
      };
      void load();
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [offset, refreshKey]);

  const summary = report?.summary;
  const average = summary?.average_rating ?? 0;
  const page = report?.reviews ?? [];
  const storeRatingById = new Map((report?.stores ?? []).map((store) => [store.store_id, store]));
  const visibleStores = [...stores].sort((a, b) => a.store_name.localeCompare(b.store_name));
  const reviewsByStore = new Map<number, StoreFeedbackReview[]>();
  for (const review of page) reviewsByStore.set(review.store_id, [...(reviewsByStore.get(review.store_id) ?? []), review]);
  return <section className="store-reviews-page">
    <section className="panel store-reviews-summary">
      <div className="store-reviews-rating-summary">
        <span className="panel-kicker">CUSTOMER RATING</span>
        <strong>{average.toFixed(1)} <small>/ 5</small></strong>
        <span className="store-review-stars" aria-label={`${average.toFixed(1)} out of 5 stars`}>{"★".repeat(Math.round(average))}{"☆".repeat(5 - Math.round(average))}</span>
        <small>{number(summary?.total ?? 0)} reviews across all stores</small>
      </div>
      <div className="store-reviews-distribution" aria-label="Rating distribution">
        {[5, 4, 3, 2, 1].map((star) => {
          const count = summary?.rating_counts[String(star)] ?? 0;
          const width = summary?.total ? count / summary.total * 100 : 0;
          return <div className="store-review-rating-row" key={star}><span>{star} ★</span><div><i style={{ width: `${width}%` }} /></div><small>{number(count)}</small></div>;
        })}
      </div>
    </section>
    {error ? <div className="error-banner" role="alert"><span>!</span>{error}</div> : null}
    <div className="store-review-feed">
      {loading && !report ? <div className="panel store-review-empty">Loading store reviews…</div> : visibleStores.map((store) => {
        const storeSummary = storeRatingById.get(store.store_id);
        const reviews = reviewsByStore.get(store.store_id) ?? [];
        return <section className="panel store-review-group" key={store.store_id}>
          <header className="store-review-group-heading">
            <div><span className="panel-kicker">STORE {store.store_id}</span><h2>{store.store_name}</h2></div>
            <div className="store-review-group-rating"><strong>{storeSummary?.review_count ? storeSummary.average_rating.toFixed(1) : "—"}</strong><span className="store-review-stars">{storeSummary?.review_count ? `${"★".repeat(Math.round(storeSummary.average_rating))}${"☆".repeat(5 - Math.round(storeSummary.average_rating))}` : "☆☆☆☆☆"}</span><small>{number(storeSummary?.review_count ?? 0)} reviews</small></div>
          </header>
          {reviews.length ? reviews.map((review) => <article className="store-review-card" key={review.feedback_id}>
            <div className="store-review-card-heading">
              <div className="store-review-customer-avatar" aria-hidden="true">{review.customer_name.trim().slice(0, 1).toUpperCase() || "C"}</div>
              <div className="store-review-customer"><strong>{review.customer_name}</strong><span>Customer review</span></div>
              <time dateTime={review.feedback_created_at}>{new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(review.feedback_created_at))}</time>
            </div>
            <div className="store-review-card-rating"><span className="store-review-stars" aria-label={`${review.rating} out of 5 stars`}>{"★".repeat(review.rating)}{"☆".repeat(5 - review.rating)}</span><span>{review.rating} out of 5</span></div>
            {review.description ? <p className="store-review-description">{review.description}</p> : null}
            <div className="store-review-card-footer">{review.order_id != null ? <span>Order #{review.order_id}</span> : null}</div>
          </article>) : <div className="store-review-group-empty">{storeSummary?.review_count ? "No reviews for this store on the current page." : "No reviews for this store yet."}</div>}
        </section>;
      })}
      {!loading && !error && !visibleStores.length ? <div className="panel store-review-empty">No stores are available.</div> : null}
    </div>
    <div className="store-reviews-pagination"><span>Showing {page.length ? `${offset + 1}–${offset + page.length}` : "0"} of {number(report?.total ?? 0)} reviews</span><div>
      <button type="button" disabled={loading || offset === 0} onClick={() => setOffset((current) => Math.max(0, current - 100))}>Previous</button>
      <button type="button" disabled={loading || offset + 100 >= (report?.total ?? 0)} onClick={() => setOffset((current) => current + 100)}>Next</button>
    </div></div>
  </section>;
}

function CustomerJourneyPage({ storeIds, refreshKey }: {
  storeIds: string[];
  refreshKey: number;
}) {
  const [report, setReport] = useState<CustomerJourneyReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      const load = async () => {
        setLoading(true);
        setError("");
        try {
          const params = new URLSearchParams();
          storeIds.forEach((storeId) => params.append("store_id", storeId));
          const response = await fetch(`${API_BASE}/api/reports/customer-journey?${params}`, { cache: "no-store" });
          if (!response.ok) {
            const body = await response.json().catch(() => null);
            throw new Error(body?.detail ?? "Could not load customer journey counts.");
          }
          if (active) setReport(await response.json() as CustomerJourneyReport);
        } catch (err) {
          if (active) setError(err instanceof Error ? err.message : "Could not load customer journey counts.");
        } finally {
          if (active) setLoading(false);
        }
      };
      void load();
    }, 200);
    return () => { active = false; window.clearTimeout(timer); };
  }, [storeIds, refreshKey]);

  const stages = report?.stages ?? [];
  const max = Math.max(1, ...stages.map((item) => item.customer_count));
  const chartWidth = 760, chartHeight = 390, left = 64, right = 20, top = 26, bottom = 55;
  const plotHeight = chartHeight - top - bottom;
  const baseline = chartHeight - bottom;
  const tickStep = Math.max(1, Math.ceil(max / 4));
  const ticks = Array.from({ length: Math.floor(max / tickStep) + 1 }, (_, index) => index * tickStep);

  return <section className="customer-journey-page">
    {error ? <div className="error-banner" role="alert"><span>!</span>{error}</div> : null}
    <article className="panel customer-journey-chart-panel">
      <div className="customer-journey-chart-heading"><div><span className="panel-kicker">{report?.scope === "selected_stores" ? (report.store_count === 1 ? report.store_name : `${number(report.store_count)} selected stores`) : "ALL STORES"}</span><h2>Customer Journey: Cart to Payment</h2></div><span>{loading ? "Updating…" : report?.refreshed_at ? `Snapshot refreshed ${new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(report.refreshed_at))}` : ""}</span></div>
      {loading && !report ? <div className="customer-journey-empty">Loading customer counts…</div> : stages.length ? <div className="customer-journey-chart-wrap">
        <svg className="customer-journey-chart" viewBox={`0 0 ${chartWidth} ${chartHeight}`} role="img" aria-label="Customer counts at cart, order, and payment stages">
          {ticks.map((tick) => {
            const y = baseline - (tick / max) * plotHeight;
            return <g key={tick}><line x1={left} x2={chartWidth - right} y1={y} y2={y} className="customer-journey-gridline"/><text x={left - 10} y={y + 4} textAnchor="end" className="customer-journey-axis-label">{number(tick)}</text></g>;
          })}
          {stages.map((item, index) => {
            const slot = (chartWidth - left - right) / stages.length;
            const barWidth = Math.min(150, slot * 0.68);
            const height = (item.customer_count / max) * plotHeight;
            const x = left + slot * index + (slot - barWidth) / 2;
            const y = baseline - height;
            return <g key={item.stage}><rect x={x} y={y} width={barWidth} height={Math.max(0, height)} rx="3" className="customer-journey-bar"><title>{`${item.stage}: ${number(item.customer_count)} customers`}</title></rect><text x={x + barWidth / 2} y={Math.max(top + 17, y - 9)} textAnchor="middle" className="customer-journey-value-label">{number(item.customer_count)}</text><text x={x + barWidth / 2} y={baseline + 27} textAnchor="middle" className="customer-journey-stage-label">{item.stage}</text></g>;
          })}
          <line x1={left} x2={chartWidth - right} y1={baseline} y2={baseline} className="customer-journey-baseline"/>
        </svg>
      </div> : <div className="customer-journey-empty">No customer journey snapshot is available for this selection.</div>}
      <p className="customer-journey-note">Counts come from the latest refreshed customer journey snapshot. This table has no event date, so the dashboard date range does not apply.{report?.scope === "selected_stores" && report.store_count > 1 ? " Counts are summed across selected stores, so a customer active at multiple stores may be counted more than once." : ""}</p>
    </article>
  </section>;
}

function CustomerLineChart({ records, metric }: { records: CustomerRecord[]; metric: "orders" | "sales" }) {
  const width = 900, height = 500, left = 74, right = 28, top = 24, bottom = 60;
  const valueOf = (record: CustomerRecord) => Number(metric === "orders" ? record.total_orders : record.total_sales) || 0;
  const max = Math.max(1, ...records.map(valueOf));
  const coords = records.map((record, index) => ({ ...record, value: valueOf(record),
    x: left + (records.length < 2 ? 0 : index / (records.length - 1) * (width - left - right)),
    y: top + (1 - valueOf(record) / max) * (height - top - bottom),
  }));
  const path = coords.reduce((result, point, index) => {
    if (index === 0) return `M${point.x},${point.y}`;
    const previous = coords[index - 1];
    const controlX = (previous.x + point.x) / 2;
    const controlY = (previous.y + point.y) / 2 + (previous.y - point.y) * 0.08;
    return `${result} Q${controlX},${controlY} ${point.x},${point.y}`;
  }, "");
  const baseline = height - bottom;
  const area = coords.length ? `${path} L${coords[coords.length - 1].x},${baseline} L${coords[0].x},${baseline} Z` : "";
  if (!records.length) return <div className="chart-empty">No customers found for this date range.</div>;
  return <div className="line-chart-wrap"><svg className={`line-chart line-chart-${metric === "orders" ? "quantity" : "value"}`} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Top customers by ${metric === "orders" ? "order count" : "sales value"}`}>
    <defs><linearGradient id={`customer-area-${metric}`} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#27a979" stopOpacity=".14"/><stop offset="100%" stopColor="#27a979" stopOpacity=".015"/></linearGradient></defs>
    {[0, 0.5, 1].map((fraction) => { const y = top + fraction * (height - top - bottom); return <g key={fraction}><line x1={left} x2={width - right} y1={y} y2={y} className="chart-gridline"/><text x={left - 9} y={y + 4} textAnchor="end" className="chart-axis-label">{metric === "sales" ? compactMoney(max * (1 - fraction)) : number(max * (1 - fraction))}</text></g>; })}
    {area ? <path d={area} className="chart-area" style={{ fill: `url(#customer-area-${metric})` }}/>:null}{path ? <path d={path} pathLength={1} className="chart-line"/>:null}
    {coords.map((point, index) => { const tooltipX = Math.min(Math.max(point.x - 150, left + 8), width - right - 316); const tooltipY = Math.max(4, point.y - 150); return <g key={`${point.customer_id}-${index}`} className="chart-point-group"><circle cx={point.x} cy={point.y} r="10" className="chart-point-halo"/><circle cx={point.x} cy={point.y} r="4.5" className="chart-point"/><foreignObject x={tooltipX} y={tooltipY} width="308" height="140" className="chart-tooltip-object"><div className="chart-tooltip"><strong>{point.customer_name}</strong><span>Customer {point.customer_id} · Last order {point.last_order_date}</span><b>{metric === "sales" ? money(point.value) : number(point.value)} <small>{metric === "sales" ? "sales value" : "orders"}</small></b></div></foreignObject></g>; })}
    {coords.map((point) => <text key={`label-${point.customer_id}`} x={point.x} y={height - 26} textAnchor="middle" className="chart-axis-label">{point.customer_id}</text>)}
    <text x={width / 2} y={height - 4} textAnchor="middle" className="chart-axis-title">Customer ID</text><text x="18" y={height / 2} textAnchor="middle" className="chart-axis-title" transform={`rotate(-90 18 ${height / 2})`}>{metric === "sales" ? "Total sales value (INR)" : "Total orders"}</text>
  </svg></div>;
}

function TopCustomersChart({ report, limit, onLimitChange }: { report: CustomerReport | null; limit: number; onLimitChange: (limit: number) => void }) {
  const orders = report?.top_by_orders ?? [];
  const sales = report?.top_by_sales ?? [];
  return <section className="panel trend-panel">
    <div className="panel-heading trend-heading"><div><span className="panel-kicker">CUSTOMER PERFORMANCE</span><h2>Top customer trends</h2></div>
      <label className="chart-limit-control"><span>Show</span><select value={limit} onChange={(event) => onLimitChange(Number(event.target.value))}><option value={5}>Top 5</option><option value={10}>Top 10</option><option value={20}>Top 20</option></select></label>
    </div>
    <div className="dual-chart-grid"><div className="metric-chart-card"><h3>Top customers by orders</h3><CustomerLineChart key={orders.map((row) => `${row.customer_id}-${row.total_orders}`).join("|")} records={orders} metric="orders"/></div>
      <div className="metric-chart-card"><h3>Top customers by sales value</h3><CustomerLineChart key={sales.map((row) => `${row.customer_id}-${row.total_sales}`).join("|")} records={sales} metric="sales"/></div></div>
    <div className="chart-caption"><span className="chart-caption-dot"/> Hover over a point to see customer details. Source: top_customers_by_order.</div>
  </section>;
}

function RepeatCustomerSegmentsChart({ report }: { report: CustomerSegmentsReport | null }) {
  const segments = report?.segments ?? [];
  const total = segments.reduce((sum, segment) => sum + segment.customer_count, 0);
  const radius = 104;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  const colors: Record<string, string> = { Repeat: "#35b88d", "One-time": "#7568df", Unknown: "#a5afc4" };
  const slices = segments.map((segment) => {
    const share = total ? segment.customer_count / total : 0;
    const slice = { ...segment, share, color: colors[segment.customer_segment] ?? colors.Unknown, offset };
    offset += circumference * share;
    return slice;
  });

  return <section className="panel repeat-segments-panel">
    <div className="panel-heading repeat-segments-heading"><div><span className="panel-kicker">CUSTOMER MIX</span><h2>Repeat and one-time customers</h2><p>Customer segments from the selected date range, grouped by last order date.</p></div>
      <span className="row-count">{number(total)} customers</span>
    </div>
    {total ? <div className="repeat-segments-content">
      <div className="repeat-donut-wrap"><svg className="repeat-donut" viewBox="0 0 320 320" role="img" aria-label={`${number(total)} customers split by repeat and one-time segment`}>
        <circle cx="160" cy="160" r={radius} className="repeat-donut-track"/>
        {slices.map((slice) => <circle key={slice.customer_segment} cx="160" cy="160" r={radius} fill="none" stroke={slice.color} strokeWidth="42" strokeDasharray={`${circumference * slice.share} ${circumference * (1 - slice.share)}`} strokeDashoffset={-slice.offset} className="repeat-donut-slice"><title>{slice.customer_segment}: {number(slice.customer_count)} customers ({number(slice.share * 100, 1)}%)</title></circle>)}
        <text x="160" y="153" textAnchor="middle" className="repeat-donut-total">{number(total)}</text><text x="160" y="179" textAnchor="middle" className="repeat-donut-label">customers</text>
      </svg></div>
      <div className="repeat-segment-legend">{slices.map((segment) => <article className="repeat-segment-card" key={segment.customer_segment}>
        <div className="repeat-segment-title"><i style={{ backgroundColor: segment.color }}/><strong>{segment.customer_segment}</strong><span>{number(segment.share * 100, 1)}%</span></div>
        <div className="repeat-segment-count">{number(segment.customer_count)} <small>customers</small></div>
        <div className="repeat-segment-details"><span>{number(segment.total_orders)} orders</span><strong>{money(segment.total_sales)} sales</strong></div>
      </article>)}</div>
    </div> : <div className="chart-empty">No customer segment data found for this date range.</div>}
    <div className="chart-caption"><span className="chart-caption-dot"/> Source: repeat_nonrepeated_customers. Store filter is unavailable for this report.</div>
  </section>;
}

function HourlyStoreSalesChart({ report, storeIds }: { report: HourlySalesReport | null; storeIds: string[] }) {
  const width = 1000;
  const height = 420;
  const left = 78;
  const right = 24;
  const top = 24;
  const bottom = 58;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const data = report?.hourly_sales ?? [];
  const max = Math.max(1, ...data.map((point) => point.total_sales_value));
  const interval = Math.pow(10, Math.floor(Math.log10(max)));
  const axisMax = Math.ceil(max / interval) * interval;
  const barWidth = plotWidth / 24 * 0.56;

  return <section className="panel hourly-sales-panel">
    <div className="panel-heading hourly-sales-heading">
      <div><span className="panel-kicker">SALES TIMING</span><h2>Hourly sales by store</h2><p>Sales value by hour across the selected date range.</p></div>
      <span className="row-count">{storeIds.length ? `${number(storeIds.length)} stores selected` : "All stores"}</span>
    </div>
    {data.length ? <div className="hourly-chart-wrap"><svg className="hourly-sales-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Hourly sales value bar chart for ${storeIds.length ? "selected stores" : "all stores"}`}>
      {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
        const y = top + (1 - fraction) * plotHeight;
        const value = axisMax * fraction;
        return <g key={fraction}><line x1={left} x2={width - right} y1={y} y2={y} className="hourly-gridline"/><text x={left - 10} y={y + 4} textAnchor="end" className="hourly-axis-label">{compactMoney(value)}</text></g>;
      })}
      {data.map((point) => {
        const x = left + (point.hour + 0.22) * plotWidth / 24;
        const barHeight = point.total_sales_value / axisMax * plotHeight;
        const y = top + plotHeight - barHeight;
        return <g key={point.hour} className="hourly-bar-group"><rect x={x} y={y} width={barWidth} height={barHeight} rx="4" className="hourly-bar"><title>{String(point.hour).padStart(2, "0")}:00 · {money(point.total_sales_value)} · {number(point.total_orders)} orders</title></rect></g>;
      })}
      {data.filter((point) => point.hour % 2 === 0).map((point) => <text key={`hour-${point.hour}`} x={left + (point.hour + 0.5) * plotWidth / 24} y={height - 30} textAnchor="middle" className="hourly-axis-label">{String(point.hour).padStart(2, "0")}</text>)}
      <text x={width / 2} y={height - 5} textAnchor="middle" className="hourly-axis-title">Hour of day</text>
      <text x="17" y={height / 2} textAnchor="middle" className="hourly-axis-title" transform={`rotate(-90 17 ${height / 2})`}>Sales value (INR)</text>
    </svg></div> : <div className="chart-empty">No hourly sales found for this date range.</div>}
    <div className="table-footer">Hover over a bar for sales and order details. The chart excludes rows without a valid hour.</div>
  </section>;
}

function StoreHourlyPivot({ report }: { report: StoreHourlyReport | null }) {
  const hours = Array.from({ length: 24 }, (_, hour) => hour);
  const storeMap = new Map<number, { store_id: number; store_name: string; cells: StoreHourlyPoint[] }>();
  for (const point of report?.rows ?? []) {
    if (!storeMap.has(point.store_id)) storeMap.set(point.store_id, { store_id: point.store_id, store_name: point.store_name, cells: Array.from({ length: 24 }, (_, hour) => ({ store_id: point.store_id, store_name: point.store_name, hour, total_sales_value: 0, total_orders: 0, total_quantity: 0 })) });
    const store = storeMap.get(point.store_id)!;
    if (point.hour >= 0 && point.hour < 24) store.cells[point.hour] = point;
  }
  const stores = [...storeMap.values()].sort((a, b) => a.store_name.localeCompare(b.store_name));
  const hourlyTotals = hours.map((hour) => stores.reduce((sum, store) => sum + store.cells[hour].total_sales_value, 0));
  const grandTotal = hourlyTotals.reduce((sum, value) => sum + value, 0);
  const maxCell = Math.max(0, ...stores.flatMap((store) => store.cells.map((cell) => cell.total_sales_value)));

  return <section className="panel store-hourly-panel">
    <div className="panel-heading store-hourly-heading"><div><span className="panel-kicker">STORE × HOUR</span><h2>Sales by store and hour</h2><p>Sales are summed across the selected dates. Hover over a cell for order and quantity totals.</p></div>
      <span className="row-count">{number(stores.length)} stores</span>
    </div>
    {stores.length ? <>
      <div className="store-hourly-table-wrap"><div className="table-scroll store-hourly-scroll"><table className="store-hourly-table">
        <thead><tr><th scope="col">Store</th>{hours.map((hour) => <th scope="col" key={hour}>{String(hour).padStart(2, "0")}:00</th>)}<th scope="col" className="store-hourly-total">Total</th></tr></thead>
        <tbody>{stores.map((store) => {
          const storeTotal = store.cells.reduce((sum, cell) => sum + cell.total_sales_value, 0);
          return <tr key={store.store_id}><th scope="row" title={store.store_name}>{store.store_name}</th>{store.cells.map((cell) => {
            const intensity = maxCell ? cell.total_sales_value / maxCell : 0;
            const background = cell.total_sales_value > 0 ? `rgba(78, 105, 216, ${0.05 + intensity * 0.42})` : undefined;
            const foreground = intensity > 0.63 ? "#fff" : "#344968";
            return <td key={cell.hour} title={`${store.store_name} · ${String(cell.hour).padStart(2, "0")}:00 · ${money(cell.total_sales_value)} · ${number(cell.total_orders)} orders · ${number(cell.total_quantity, 2)} units`} style={{ backgroundColor: background, color: foreground }}>{cell.total_sales_value > 0 ? compactMoney(cell.total_sales_value) : "—"}</td>;
          })}<td className="store-hourly-total">{money(storeTotal)}</td></tr>;
        })}</tbody>
        <tfoot><tr><th scope="row">All stores</th>{hourlyTotals.map((value, hour) => <td key={hour}>{money(value)}</td>)}<td className="store-hourly-total">{money(grandTotal)}</td></tr></tfoot>
      </table></div></div>
      <div className="store-hourly-heat-legend"><span>Lower sales</span><i/><span>Higher sales</span><strong>Selected range total: {money(grandTotal)}</strong></div>
    </> : <div className="chart-empty">No store-hour sales found for this date range.</div>}
    <div className="table-footer">Source: store_hourly_sales · Cell color scales with sales value. Store filter applies.</div>
  </section>;
}

const FOOD_TYPE_COLORS = ["#7867e8", "#39b8d4", "#e4a84f", "#e26f91", "#48b88b", "#5689e6", "#d178d1", "#9caa45", "#e27c4b", "#4bafaa", "#aa79e5", "#c5a24c", "#6485a5", "#db6870", "#66a96a", "#9387af"];

function FoodTypeSalesChart({ report }: { report: FoodTypeReport | null }) {
  // MySQL DECIMAL values may arrive as strings; coerce them before chart math
  // so percentages and SVG arc coordinates stay finite.
  const rows = (report?.food_types ?? []).map((row) => ({
    ...row,
    total_sales_value: Number(String(row.total_sales_value ?? 0).replace(/[₹,\s]/g, "")),
  })).filter((row) => Number.isFinite(row.total_sales_value) && row.total_sales_value > 0);
  const total = rows.reduce((sum, row) => sum + row.total_sales_value, 0);
  let angle = -Math.PI / 2;
  const slices = rows.map((row, index) => {
    const start = angle;
    const fraction = total ? row.total_sales_value / total : 0;
    angle += fraction * Math.PI * 2;
    const end = angle;
    const radius = 150;
    const x1 = 180 + radius * Math.cos(start);
    const y1 = 180 + radius * Math.sin(start);
    const x2 = 180 + radius * Math.cos(end);
    const y2 = 180 + radius * Math.sin(end);
    const path = fraction >= 0.999999 ? `M 180 30 A 150 150 0 1 1 179.99 30 Z` : `M 180 180 L ${x1} ${y1} A ${radius} ${radius} 0 ${fraction > 0.5 ? 1 : 0} 1 ${x2} ${y2} Z`;
    return { ...row, path, color: FOOD_TYPE_COLORS[index % FOOD_TYPE_COLORS.length], share: fraction };
  });

  return <section className="panel food-type-panel">
    <div className="panel-heading food-type-heading">
      <div><span className="panel-kicker">CATEGORY MIX</span><h2>Sales by food type</h2><p>Share of total sales value for the selected date range.</p></div>
      <span className="row-count">{number(rows.length)} food types</span>
    </div>
    {slices.length ? <div className="food-type-content">
      <div className="food-pie-wrap"><svg className="food-pie" viewBox="0 0 360 360" role="img" aria-label="Pie chart showing sales value by food type">
        {slices.map((slice) => <path key={slice.food_type_name} d={slice.path} fill={slice.color} className="food-pie-slice"><title>{slice.food_type_name}: {money(slice.total_sales_value)} ({number(slice.share * 100, 1)}%)</title></path>)}
      </svg></div>
      <div className="food-type-legend" aria-label="Food type sales legend">
        {slices.map((slice) => <div className="food-type-legend-row" key={slice.food_type_name}>
          <span className="food-type-swatch" style={{ backgroundColor: slice.color }} />
          <span className="food-type-name" title={slice.food_type_name}>{slice.food_type_name}</span>
          <strong>{money(slice.total_sales_value)}</strong>
          <small>{number(slice.share * 100, 1)}%</small>
        </div>)}
      </div>
    </div> : <div className="chart-empty">No food type sales found for this date range.</div>}
    <div className="chart-caption"><span className="chart-caption-dot" /> Total sales value: {money(total)} · All stores</div>
    <div className="table-footer">Source: food_type_sales · Store-level filtering is unavailable for this report.</div>
  </section>;
}

const STORE_METRICS: { value: StoreMetric; label: string; currency: boolean }[] = [
  { value: "item_sales_value", label: "Total sales value", currency: true },
  { value: "total_quantity", label: "Total quantity", currency: false },
];

function StorePerformanceChart({ stores, metric, limit, onMetricChange, onLimitChange }: { stores: StoreRow[]; metric: StoreMetric; limit: 5 | 10; onMetricChange: (metric: StoreMetric) => void; onLimitChange: (limit: 5 | 10) => void }) {
  const metricInfo = STORE_METRICS.find((item) => item.value === metric)!;
  const rows = [...stores]
    .sort((a, b) => b[metric] - a[metric])
    .slice(0, limit)
    .sort((a, b) => a[metric] - b[metric] || a.store_name.localeCompare(b.store_name));
  const width = 1000;
  const height = 420;
  const left = 86;
  const right = 34;
  const top = 28;
  const bottom = 78;
  const max = Math.max(1, ...rows.map((row) => row[metric]));
  const coords = rows.map((row, index) => ({
    ...row,
    x: left + (rows.length < 2 ? (width - left - right) / 2 : (index / (rows.length - 1)) * (width - left - right)),
    y: top + (1 - row[metric] / max) * (height - top - bottom),
  }));
  const linePath = coords.reduce((path, point, index) => {
    if (!index) return `M${point.x},${point.y}`;
    const previous = coords[index - 1];
    const controlX = (previous.x + point.x) / 2;
    const controlY = (previous.y + point.y) / 2 + (previous.y - point.y) * 0.04;
    return `${path} Q${controlX},${controlY} ${point.x},${point.y}`;
  }, "");
  const baseline = height - bottom;
  const areaPath = coords.length ? `${linePath} L${coords[coords.length - 1].x},${baseline} L${coords[0].x},${baseline} Z` : "";
  return <section className="panel store-performance-panel">
    <div className="panel-heading store-chart-heading">
      <div><span className="panel-kicker">STORE COMPARISON</span><h2>{metricInfo.label} by store</h2></div>
      <div className="store-chart-controls">
        <label className="chart-limit-control"><span>Metric</span><select value={metric} onChange={(event) => onMetricChange(event.target.value as StoreMetric)}>
          {STORE_METRICS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select></label>
        <label className="chart-limit-control"><span>Show</span><select value={limit} onChange={(event) => onLimitChange(Number(event.target.value) as 5 | 10)}>
          <option value={5}>Top 5</option><option value={10}>Top 10</option>
        </select></label>
      </div>
    </div>
    {rows.length ? <div className="store-line-chart-wrap"><svg className="store-line-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${metricInfo.label} by store line chart`}>
      <defs><linearGradient id="store-area" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#27a979" stopOpacity=".22"/><stop offset="100%" stopColor="#27a979" stopOpacity=".015"/></linearGradient><linearGradient id="store-line-gradient" x1="0" x2="1" y1="0" y2="0"><stop offset="0%" stopColor="#087b58"/><stop offset="100%" stopColor="#4cbf8b"/></linearGradient></defs>
      {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
        const y = top + fraction * (height - top - bottom);
        const value = max * (1 - fraction);
        return <g key={fraction}><line x1={left} x2={width - right} y1={y} y2={y} className="chart-gridline"/><text x={left - 10} y={y + 5} textAnchor="end" className="store-chart-axis-label">{metricInfo.currency ? compactMoney(value) : number(value)}</text></g>;
      })}
      {areaPath ? <path d={areaPath} className="chart-area"/> : null}
      {linePath ? <path d={linePath} pathLength={1} className="chart-line store-chart-line"/> : null}
      {coords.map((point, index) => {
        const tooltipX = Math.min(Math.max(point.x - 150, left + 8), width - right - 316);
        const tooltipY = Math.max(4, point.y - 142);
        return <g key={point.store_id} className="chart-point-group"><circle cx={point.x} cy={point.y} r="14" className="chart-point-halo"/><circle cx={point.x} cy={point.y} r="6" className="chart-point store-chart-point"/><text x={point.x} y={point.y - 18} textAnchor="middle" className="store-point-value">{metricInfo.currency ? compactMoney(point[metric]) : number(point[metric])}</text><foreignObject x={tooltipX} y={tooltipY} width="308" height="132" className="chart-tooltip-object"><div className="chart-tooltip"><strong>{point.store_name}</strong><span>Store ID {point.store_id}</span><b>{metricInfo.currency ? money(point[metric]) : number(point[metric])} <small>{metricInfo.label.toLowerCase()}</small></b></div></foreignObject></g>;
      })}
      {coords.map((point) => <text key={`label-${point.store_id}`} x={point.x} y={height - 38} textAnchor="middle" className="store-chart-axis-label store-name-axis-label">{point.store_name.length > 20 ? `${point.store_name.slice(0, 19)}…` : point.store_name}<title>{point.store_name}</title></text>)}
      <text x={width / 2} y={height - 8} textAnchor="middle" className="store-chart-axis-title">Store</text>
      <text x="18" y={height / 2} textAnchor="middle" className="store-chart-axis-title" transform={`rotate(-90 18 ${height / 2})`}>{metric === "item_sales_value" ? "Total sales value (INR)" : "Total quantity per store"}</text>
    </svg></div> : <div className="chart-empty">No store performance found for this range.</div>}
    <div className="chart-caption"><span className="chart-caption-dot"/> Stores run from lower to higher values. Hover for store details.</div>
    <div className="table-footer">{stores.length ? `Showing ${number(rows.length)} of ${number(stores.length)} stores · ${metricInfo.label}` : "Select a date range"}</div>
  </section>;
}

function InventoryDemandList({ report }: { report: InventoryReport | null }) {
  const points = [...(report?.points ?? [])].sort((a, b) =>
    a.stock_quantity - b.stock_quantity
      || b.total_sales_value - a.total_sales_value
      || a.sku_id - b.sku_id,
  );
  return <section className="panel inventory-panel">
    <div className="panel-heading inventory-heading">
      <div><span className="panel-kicker">STOCK + SALES BY SKU</span><h2>Available stock and total sales</h2><p>Latest stock snapshot with sales totals for the selected date range.</p></div>
      <span className="row-count">{number(points.length)} SKU / store records</span>
    </div>
    {points.length ? <div className="restock-table-wrap"><table className="restock-table">
      <thead><tr><th>Rank</th><th>SKU ID</th><th>SKU name</th><th>Store</th><th>Available quantity</th><th>Total quantity sold</th><th>Total sales value</th><th>Stock snapshot date</th></tr></thead>
      <tbody>{points.map((point, index) => {
        return <tr key={`${point.sku_id}-${point.store_id}`}>
          <td><span className={`restock-rank ${index < 3 ? "restock-rank-highlight" : ""}`}>{String(index + 1).padStart(2, "0")}</span></td>
          <td>{point.sku_id}</td>
          <td><span className="restock-sku"><strong title={point.sku_name}>{point.sku_name}</strong></span></td>
          <td><span className="restock-store" title={point.store_name}>{point.store_name}</span></td>
          <td className="numeric quantity-cell">{number(point.stock_quantity, 0)}</td>
          <td className="numeric">{number(point.total_quantity, 0)}</td>
          <td className="numeric sales-value-cell">{money(point.total_sales_value)}</td>
          <td>{prettyDate(point.report_date)}</td>
        </tr>;
      })}</tbody>
    </table></div> : <div className="inventory-empty"><strong>No recent sales records for this selection</strong><span>Try All time or clear the store filter to view other SKU and store records.</span></div>}
    <div className="chart-caption"><span className="chart-caption-dot"/> Sorted by available quantity, lowest first. Sales totals follow the selected date range.</div>
    <div className="table-footer">{report ? `Sales period: ${prettyDate(report.start_date)} – ${prettyDate(report.end_date)} · stock is the latest available snapshot on or before the end date.` : "Source: DuckDB sales_item_facts and inventory_demand_daily."}</div>
  </section>;
}

function groupQuantityRows(rows: Report["quantity_records"]) {
  const grouped = new Map<number, { sku_id: number; sku_name: string; quantity: number }>();
  for (const row of rows) {
    const existing = grouped.get(row.sku_id);
    if (existing) existing.quantity += row.quantity;
    else grouped.set(row.sku_id, { sku_id: row.sku_id, sku_name: row.sku_name, quantity: row.quantity });
  }
  return [...grouped.values()].sort((a, b) => b.quantity - a.quantity).map((row, index) => ({ ...row, rank: index + 1 }));
}

function groupValueRows(rows: Report["value_records"]) {
  const grouped = new Map<number, { sku_id: number; sku_name: string; sales_value: number }>();
  for (const row of rows) {
    const existing = grouped.get(row.sku_id);
    if (existing) existing.sales_value += row.sales_value;
    else grouped.set(row.sku_id, { sku_id: row.sku_id, sku_name: row.sku_name, sales_value: row.sales_value });
  }
  return [...grouped.values()].sort((a, b) => b.sales_value - a.sales_value).map((row, index) => ({ ...row, rank: index + 1 }));
}

function StorePicker({ stores, value, onChange }: {
  stores: { store_id: number; store_name: string }[];
  value: string[];
  onChange: (storeIds: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const selectedStores = stores.filter((store) => value.includes(String(store.store_id)));
  const selectedStore = selectedStores[0];

  useEffect(() => {
    function handlePointerDown(event: PointerEvent) {
      if (pickerRef.current && !pickerRef.current.contains(event.target as Node)) setOpen(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  function toggleStore(storeId: string) {
    onChange(value.includes(storeId) ? value.filter((id) => id !== storeId) : [...value, storeId]);
  }

  function chooseAllStores() {
    onChange([]);
    setOpen(false);
    triggerRef.current?.focus();
  }

  return <div className={`store-picker ${open ? "store-picker-open" : ""}`} ref={pickerRef}>
    <span className="store-field-label">Store</span>
    <button ref={triggerRef} type="button" className="store-picker-trigger" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)}>
      <span className="store-picker-emblem" aria-hidden="true">⌖</span>
      <span className="store-picker-current"><small>{selectedStores.length ? `${selectedStores.length} SELECTED` : "FILTER BY STORE"}</small><strong>{selectedStores.length > 1 ? `${selectedStores.length} stores selected` : selectedStore?.store_name ?? "All stores"}</strong></span>
      <span className="store-picker-chevron" aria-hidden="true">⌄</span>
    </button>
    {open ? <div className="store-picker-menu" role="group" aria-label="Choose stores">
      <div className="store-picker-menu-heading"><span>SELECT LOCATION</span><span>{String(stores.length).padStart(2, "0")} STORES</span></div>
      <button type="button" aria-pressed={!value.length} className={`store-picker-option ${!value.length ? "store-picker-option-selected" : ""}`} onClick={chooseAllStores}>
        <span className="store-option-icon">✦</span><span className="store-option-copy"><strong>All stores</strong><small>View network wide performance</small></span><span className="store-option-check">{!value.length ? "✓" : ""}</span>
      </button>
      {stores.map((store) => {
        const selected = value.includes(String(store.store_id));
        return <button key={store.store_id} type="button" aria-pressed={selected} className={`store-picker-option ${selected ? "store-picker-option-selected" : ""}`} onClick={() => toggleStore(String(store.store_id))}>
          <span className="store-option-icon">⌖</span><span className="store-option-copy"><strong>{store.store_name}</strong><small>STORE ID · {String(store.store_id).padStart(3, "0")}</small></span><span className="store-option-check">{selected ? "✓" : ""}</span>
        </button>;
      })}
      {!stores.length ? <div className="store-picker-empty">No store locations available.</div> : null}
      <div className="store-picker-footer"><span>{value.length ? `${value.length} ${value.length === 1 ? "store" : "stores"} selected` : "No store filter"}</span><button type="button" onClick={() => setOpen(false)}>Done</button></div>
    </div> : null}
  </div>;
}

function DatabaseTableBrowser({
  tables, selectedTable, onSelect, preview, offset, onOffsetChange, loading, error, syncStatus,
}: {
  tables: DatabaseTable[];
  selectedTable: string;
  onSelect: (name: string) => void;
  preview: TablePreview | null;
  offset: number;
  onOffsetChange: (offset: number) => void;
  loading: boolean;
  error: string;
  syncStatus: SyncStatus;
}) {
  const active = tables.find((table) => table.table_name === selectedTable);
  return <section className="db-browser">
    <aside className="panel db-table-list">
      <div className="db-browser-heading"><span className="panel-kicker">DATABASE TABLES</span><strong>{tables.length}</strong></div>
      {loading && !tables.length ? <div className="db-browser-empty">Loading MySQL tables…</div> : null}
      {!loading && !tables.length && !error ? <div className="db-browser-empty">No tables found in this database.</div> : null}
      {tables.map((table) => <button key={table.table_name} className={`db-table-choice ${selectedTable === table.table_name ? "db-table-choice-active" : ""}`} onClick={() => onSelect(table.table_name)}>
        <span><strong>{table.table_name}</strong><small>{table.columns.length} columns</small></span><span aria-hidden="true">›</span>
      </button>)}
    </aside>
    <div className="db-browser-main">
      {error ? <div className="error-banner" role="alert"><span>!</span>{error}</div> : null}
      {active ? <>
        <section className="panel db-schema-card">
          <div className="db-content-heading"><div><span className="panel-kicker">TABLE SCHEMA</span><h2>{active.table_name}</h2></div><span className="row-count">{active.columns.length} columns</span></div>
          <div className="db-columns-grid">{active.columns.map((column) => <div className="db-column" key={column.column_name}>
            <strong>{column.column_name}{column.key === "PRI" ? <span className="db-key">PK</span> : null}</strong>
            <span>{column.column_type}{column.is_nullable ? " · nullable" : " · required"}</span>
          </div>)}</div>
        </section>
        <section className="panel db-rows-card">
          <div className="db-content-heading"><div><span className="panel-kicker">DATA PREVIEW</span><h2>Rows {preview?.rows.length ? `${offset + 1}–${offset + preview.rows.length}` : ""}</h2></div><div className="db-pagination"><button disabled={offset === 0 || loading} onClick={() => onOffsetChange(Math.max(0, offset - 100))}>Previous</button><button disabled={loading || (preview?.rows.length ?? 0) < 100} onClick={() => onOffsetChange(offset + 100)}>Next</button></div></div>
          {syncStatus.status === "succeeded" ? <div className="sync-message sync-success">Sync completed successfully. The table preview has been refreshed.</div> : syncStatus.status === "failed" ? <div className="sync-message sync-failure">Sync failed (exit code {syncStatus.return_code ?? "unknown"}). Check `E:\\fetching\\logs\\incremental_sync.log`.</div> : null}
          <div className="table-scroll db-data-scroll">{preview?.columns.length ? <table><thead><tr>{preview.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>
            {preview.rows.map((row, rowIndex) => <tr key={`${offset}-${rowIndex}`}>{row.map((value, columnIndex) => <td key={`${rowIndex}-${columnIndex}`} title={value == null ? "NULL" : String(value)}>{value == null ? <span className="db-null">NULL</span> : String(value)}</td>)}</tr>)}
          </tbody></table> : <div className="db-browser-empty">{loading ? "Loading rows…" : "This table has no rows."}</div>}</div>
        </section>
      </> : !error ? <div className="panel db-browser-empty db-browser-placeholder">Select a table to inspect its data.</div> : null}
    </div>
  </section>;
}

export default function App() {
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [storeIds, setStoreIds] = useState<string[]>([]);
  const [chartLimit, setChartLimit] = useState(10);
  const [reportView, setReportView] = useState<"chart" | "table">("chart");
  const [activeSection, setActiveSection] = useState<"tables" | "sku" | "store" | "inventory" | "food-type" | "hourly" | "store-hourly" | "customers" | "repeat-customers" | "high-inventory" | "inventory-alerts" | "store-reviews" | "customer-journey">("tables");
  const [databaseTables, setDatabaseTables] = useState<DatabaseTable[]>([]);
  const [selectedTable, setSelectedTable] = useState("");
  const [tablePreview, setTablePreview] = useState<TablePreview | null>(null);
  const [tableOffset, setTableOffset] = useState(0);
  const [tablesLoading, setTablesLoading] = useState(true);
  const [tableError, setTableError] = useState("");
  const [syncStatus, setSyncStatus] = useState<SyncStatus>({ status: "idle", pid: null, started_at: null, finished_at: null, return_code: null, updated_tables: [] });
  const [syncToast, setSyncToast] = useState<"success" | "failure" | null>(null);
  const syncWasRunning = useRef(false);
  const [storeMetric, setStoreMetric] = useState<StoreMetric>("item_sales_value");
  const [storeLimit, setStoreLimit] = useState<5 | 10>(5);
  const [report, setReport] = useState<Report | null>(null);
  const [storeReport, setStoreReport] = useState<StoreReport | null>(null);
  const [inventoryReport, setInventoryReport] = useState<InventoryReport | null>(null);
  const [foodTypeReport, setFoodTypeReport] = useState<FoodTypeReport | null>(null);
  const [hourlyReport, setHourlyReport] = useState<HourlySalesReport | null>(null);
  const [storeHourlyReport, setStoreHourlyReport] = useState<StoreHourlyReport | null>(null);
  const [customerReport, setCustomerReport] = useState<CustomerReport | null>(null);
  const [customerSegmentsReport, setCustomerSegmentsReport] = useState<CustomerSegmentsReport | null>(null);
  const [highInventoryReport, setHighInventoryReport] = useState<HighInventoryReport | null>(null);
  const [inventoryAlertsReport, setInventoryAlertsReport] = useState<InventoryAlertsReport | null>(null);
  const [inventoryAlertOffset, setInventoryAlertOffset] = useState(0);
  const [storeReviewsRefreshKey, setStoreReviewsRefreshKey] = useState(0);
  const [customerJourneyRefreshKey, setCustomerJourneyRefreshKey] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const quantityTableRows = groupQuantityRows(report?.quantity_records ?? []);
  const valueTableRows = groupValueRows(report?.value_records ?? []);

  async function loadRange(start: string, end: string, selectedChartLimit = chartLimit, selectedStoreIds = storeIds, quiet = false) {
    if (!start || !end || start > end) {
      if (!quiet) setError("Choose a valid date range.");
      return;
    }
    setStartDate(start);
    setEndDate(end);
    if (!quiet) {
      setLoading(true);
      setError("");
    }
    try {
      if (activeSection === "sku") setReport(await getReport(start, end, selectedChartLimit, selectedStoreIds));
      else if (activeSection === "store") setStoreReport(await getStoreReport(start, end, selectedStoreIds));
      else if (activeSection === "inventory") setInventoryReport(await getInventoryReport(start, end, selectedStoreIds));
      else if (activeSection === "food-type") setFoodTypeReport(await getFoodTypeReport(start, end, selectedStoreIds));
      else if (activeSection === "hourly") setHourlyReport(await getHourlySalesReport(start, end, selectedStoreIds));
      else if (activeSection === "store-hourly") setStoreHourlyReport(await getStoreHourlyReport(start, end, selectedStoreIds));
      else if (activeSection === "customers") setCustomerReport(await getTopCustomersReport(start, end, selectedChartLimit, selectedStoreIds));
      else if (activeSection === "repeat-customers") setCustomerSegmentsReport(await getCustomerSegmentsReport(start, end, selectedStoreIds));
      else if (activeSection === "high-inventory") setHighInventoryReport(await getHighInventoryReport(start, end, selectedStoreIds));
      else if (activeSection === "inventory-alerts") {
        setInventoryAlertOffset(0);
        setInventoryAlertsReport(await getInventoryAlertsReport(start, end, selectedStoreIds));
      }
      else if (activeSection === "store-reviews") setStoreReviewsRefreshKey((current) => current + 1);
      else if (activeSection === "customer-journey") setCustomerJourneyRefreshKey((current) => current + 1);
    } catch (err) {
      if (!quiet) {
        setError(err instanceof Error ? err.message : "Could not load the report.");
        if (activeSection === "sku") setReport(null);
        else if (activeSection === "store") setStoreReport(null);
        else if (activeSection === "inventory") setInventoryReport(null);
        else if (activeSection === "food-type") setFoodTypeReport(null);
        else if (activeSection === "hourly") setHourlyReport(null);
        else if (activeSection === "store-hourly") setStoreHourlyReport(null);
        else if (activeSection === "customers") setCustomerReport(null);
        else if (activeSection === "repeat-customers") setCustomerSegmentsReport(null);
        else if (activeSection === "high-inventory") setHighInventoryReport(null);
        else if (activeSection === "inventory-alerts") setInventoryAlertsReport(null);
      }
    } finally {
      if (!quiet) setLoading(false);
    }
  }

  async function changeInventoryAlertPage(offset: number) {
    if (!startDate || !endDate) return;
    setLoading(true);
    setError("");
    try {
      setInventoryAlertsReport(await getInventoryAlertsReport(startDate, endDate, storeIds, offset));
      setInventoryAlertOffset(offset);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load inventory alert tables.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    fetch(`${API_BASE}/api/metadata`)
      .then(async (response) => {
        if (!response.ok) throw new Error("Cannot connect to the analytics backend.");
        return (await response.json()) as Metadata;
      })
      .then((data) => {
        if (!active) return;
        setMetadata(data);
        if (data.latest_date && activeSection !== "tables") {
          const [start, end] = lastSevenDays(data.latest_date);
          void loadRange(start, end);
        }
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : "Cannot connect to the backend.");
      });
    return () => {
      active = false;
    };
    // Initial load is anchored to the max date returned by the backend.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (activeSection === "tables" || !metadata?.latest_date) return;
    const [defaultStart, defaultEnd] = lastSevenDays(metadata.latest_date);
    const start = startDate || defaultStart;
    const end = endDate || defaultEnd;
    void loadRange(start, end);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSection, metadata?.latest_date]);

  // Refresh visible report data while the dashboard is open. If the current
  // range was anchored to the previous latest sales date, extend it as new
  // dates arrive; manually chosen historical ranges remain unchanged.
  useEffect(() => {
    if (activeSection === "tables") return;
    const refresh = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/metadata`, { cache: "no-store" });
        if (!response.ok) return;
        const freshMetadata = await response.json() as Metadata;
        let nextStart = startDate;
        let nextEnd = endDate;
        const previousLatestDate = metadata?.latest_date;
        const followedLatestDate = Boolean(previousLatestDate && endDate === previousLatestDate);
        if (freshMetadata.latest_date && previousLatestDate && followedLatestDate && freshMetadata.latest_date !== previousLatestDate) {
          const [oldSevenDayStart] = lastSevenDays(previousLatestDate);
          const [newSevenDayStart] = lastSevenDays(freshMetadata.latest_date);
          if (startDate === oldSevenDayStart) nextStart = newSevenDayStart;
          nextEnd = freshMetadata.latest_date;
          if (!nextStart) nextStart = newSevenDayStart;
          if (nextStart !== startDate) setStartDate(nextStart);
          setEndDate(nextEnd);
        }
        setMetadata(freshMetadata);
        if (nextStart && nextEnd && nextStart <= nextEnd) {
          await loadRange(nextStart, nextEnd, chartLimit, storeIds, true);
        }
      } catch {
        // Keep the last successful chart visible if a background refresh fails.
      }
    };
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(timer);
    // Refresh uses current filters; the latest-date dependency re-arms the poll
    // only when the underlying date range's anchor changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSection, startDate, endDate, chartLimit, storeIds, metadata?.latest_date]);

  // A successful incremental sync should update the visible chart immediately.
  useEffect(() => {
    if (syncStatus.status !== "succeeded" || !syncStatus.finished_at || activeSection === "tables") return;
    let active = true;
    void (async () => {
      try {
        const response = await fetch(`${API_BASE}/api/metadata`, { cache: "no-store" });
        if (!response.ok) return;
        const freshMetadata = await response.json() as Metadata;
        if (!active) return;
        setMetadata(freshMetadata);
        const previousLatestDate = metadata?.latest_date;
        const shouldAdvanceEnd = Boolean(previousLatestDate && endDate === previousLatestDate);
        const nextEnd = shouldAdvanceEnd ? freshMetadata.latest_date ?? endDate : endDate;
        let nextStart = startDate || (nextEnd ? lastSevenDays(nextEnd)[0] : "");
        if (shouldAdvanceEnd && previousLatestDate && nextEnd !== previousLatestDate && startDate === lastSevenDays(previousLatestDate)[0]) {
          nextStart = lastSevenDays(nextEnd)[0];
        }
        if (nextStart && nextEnd && nextStart <= nextEnd) {
          if (nextStart !== startDate) setStartDate(nextStart);
          if (nextEnd !== endDate) setEndDate(nextEnd);
          await loadRange(nextStart, nextEnd, chartLimit, storeIds, true);
        }
      } catch {
        // The regular background refresh will retry without clearing the chart.
      }
    })();
    return () => { active = false; };
    // Sync completion is the trigger; read the currently selected range once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncStatus.status, syncStatus.finished_at]);

  useEffect(() => {
    let active = true;
    fetch(`${API_BASE}/api/tables`)
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load tables from MySQL.");
        return (await response.json()) as { tables: DatabaseTable[] };
      })
      .then((data) => {
        if (!active) return;
        setDatabaseTables(data.tables);
        setSelectedTable((current) => current || data.tables[0]?.table_name || "");
      })
      .catch((err: unknown) => setTableError(err instanceof Error ? err.message : "Could not load MySQL tables."))
      .finally(() => { if (active) setTablesLoading(false); });
    return () => { active = false; };
  }, [syncStatus.finished_at]);

  useEffect(() => {
    if (!selectedTable) return;
    let active = true;
    setTablesLoading(true);
    fetch(`${API_BASE}/api/tables/${encodeURIComponent(selectedTable)}/rows?limit=100&offset=${tableOffset}`)
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load rows for this MySQL table.");
        return (await response.json()) as TablePreview;
      })
      .then((data) => { if (active) { setTablePreview(data); setTableError(""); } })
      .catch((err: unknown) => { if (active) setTableError(err instanceof Error ? err.message : "Could not read this table."); })
      .finally(() => { if (active) setTablesLoading(false); });
    return () => { active = false; };
  }, [selectedTable, tableOffset, syncStatus.finished_at]);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/sync/status`);
        if (!response.ok) return;
        const state = (await response.json()) as SyncStatus;
        if (!active) return;
        setSyncStatus(state);
        if (syncWasRunning.current && (state.status === "succeeded" || state.status === "failed")) {
          syncWasRunning.current = false;
          setSyncToast(state.status === "succeeded" ? "success" : "failure");
        }
        if (state.status === "running") timer = window.setTimeout(poll, 2000);
      } catch { /* The table browser reports API connectivity errors separately. */ }
    };
    void poll();
    return () => { active = false; if (timer) window.clearTimeout(timer); };
  }, [syncStatus.status]);

  useEffect(() => {
    if (!syncToast) return;
    const timer = window.setTimeout(() => setSyncToast(null), 7000);
    return () => window.clearTimeout(timer);
  }, [syncToast]);

  async function runSync() {
    try {
      const response = await fetch(`${API_BASE}/api/sync/run`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "Could not start the sync.");
      syncWasRunning.current = true;
      setSyncToast(null);
      setSyncStatus(result as SyncStatus);
    } catch (err) {
      setTableError(err instanceof Error ? err.message : "Could not start the sync.");
      setSyncToast("failure");
    }
  }

  const invalidRange = !startDate || !endDate || startDate > endDate;

  function applyLastSevenDays() {
    if (!metadata?.latest_date) return;
    const [start, end] = lastSevenDays(metadata.latest_date);
    void loadRange(start, end);
  }

  function applyLastMonth() {
    if (!metadata?.latest_date) return;
    const [start, end] = lastCalendarMonth(metadata.latest_date);
    void loadRange(start, end);
  }

  function applyAllTime() {
    if (!metadata?.min_date || !metadata.latest_date) return;
    void loadRange(metadata.min_date, metadata.latest_date);
  }

  const reportTitles = {
    sku: "SKU Performance",
    store: "Store Performance",
    inventory: "Inventory Demand",
    "food-type": "Food Type Sales",
    hourly: "Hourly Sales",
    "store-hourly": "Store by Hour Sales",
    customers: "Top Customers",
    "repeat-customers": "Repeat Customers",
    "high-inventory": "High Inventory, Low Sales",
    "inventory-alerts": "Inventory Alerts",
    "store-reviews": "Store Reviews",
    "customer-journey": "Customer Journey",
  } as const;
  let visibleReportData: unknown = null;
  if (!loading) {
    if (activeSection === "sku" && report) {
      visibleReportData = {
        summary: { total_skus: report.total_skus, total_quantity: report.total_quantity, total_orders: report.total_orders, total_sales_value: report.total_sales_value },
        quantity_trend: report.quantity_trend.slice(0, 100),
        sales_trend: report.trend.slice(0, 100),
        quantity_table: quantityTableRows.slice(0, 100),
        sales_table: valueTableRows.slice(0, 100),
      };
    } else if (activeSection === "store" && storeReport) {
      const storesInChart = [...storeReport.stores]
        .sort((a, b) => b[storeMetric] - a[storeMetric])
        .slice(0, storeLimit)
        .sort((a, b) => a[storeMetric] - b[storeMetric] || a.store_name.localeCompare(b.store_name));
      visibleReportData = { metric: storeMetric, visible_store_limit: storeLimit, stores: storesInChart };
    } else if (activeSection === "inventory" && inventoryReport) visibleReportData = { points: inventoryReport.points.slice(0, 100) };
    else if (activeSection === "food-type" && foodTypeReport) visibleReportData = { food_types: foodTypeReport.food_types.slice(0, 100) };
    else if (activeSection === "hourly" && hourlyReport) visibleReportData = { hourly_sales: hourlyReport.hourly_sales, unassigned: hourlyReport.unassigned };
    else if (activeSection === "store-hourly" && storeHourlyReport) visibleReportData = { rows: storeHourlyReport.rows.slice(0, 200) };
    else if (activeSection === "customers" && customerReport) visibleReportData = { top_by_orders: customerReport.top_by_orders.slice(0, chartLimit), top_by_sales: customerReport.top_by_sales.slice(0, chartLimit) };
    else if (activeSection === "repeat-customers" && customerSegmentsReport) visibleReportData = { segments: customerSegmentsReport.segments };
    else if (activeSection === "high-inventory" && highInventoryReport) visibleReportData = { candidate_count: highInventoryReport.candidate_count, items: highInventoryReport.items.slice(0, 100) };
    else if (activeSection === "inventory-alerts" && inventoryAlertsReport) visibleReportData = inventoryAlertsReport;
  }
  const chartContext = activeSection === "tables" || activeSection === "store-reviews" || activeSection === "customer-journey" ? "" : JSON.stringify({
    report: reportTitles[activeSection],
    filters: {
      start_date: startDate || null,
      end_date: endDate || null,
      stores: storeIds.map((id) => metadata?.stores.find((store) => String(store.store_id) === id)?.store_name ?? id),
    },
    view: activeSection === "sku" ? reportView : "chart",
    chart_data_loading: loading,
    data: visibleReportData,
  });

  return (
    <main className={`page-shell${activeSection === "store-reviews" || activeSection === "customer-journey" ? " page-shell-scrollable" : ""}`}>
      {syncToast ? <div className={`sync-toast sync-toast-${syncToast}`} role="status" aria-live="polite">
        <span className="sync-toast-icon" aria-hidden="true">{syncToast === "success" ? "✓" : "!"}</span>
        <span><strong>{syncToast === "success" ? "Sync complete" : "Sync failed"}</strong><small>{syncToast === "success" ? syncStatus.updated_tables?.length ? `${syncStatus.updated_tables.length} ${syncStatus.updated_tables.length === 1 ? "table" : "tables"} updated: ${syncStatus.updated_tables.join(", ")}` : "No tables were updated." : "The sync could not complete. Check the sync log for details."}</small></span>
        <button type="button" aria-label="Dismiss notification" onClick={() => setSyncToast(null)}>×</button>
      </div> : null}
      <nav className="main-nav" aria-label="Analytics reports">
        <button className={activeSection === "tables" ? "main-nav-active" : ""} onClick={() => setActiveSection("tables")}>MySQL Tables</button>
        <button className={activeSection === "sku" ? "main-nav-active" : ""} onClick={() => setActiveSection("sku")}>SKU Performance</button>
        <button className={activeSection === "store" ? "main-nav-active" : ""} onClick={() => setActiveSection("store")}>Store Performance</button>
        <button className={activeSection === "food-type" ? "main-nav-active" : ""} onClick={() => setActiveSection("food-type")}>Food Type Sales</button>
        <button className={activeSection === "hourly" ? "main-nav-active" : ""} onClick={() => setActiveSection("hourly")}>Hourly Sales</button>
        <button className={activeSection === "store-hourly" ? "main-nav-active" : ""} onClick={() => setActiveSection("store-hourly")}>Store × Hour</button>
        <button className={activeSection === "customers" ? "main-nav-active" : ""} onClick={() => setActiveSection("customers")}>Top Customers</button>
        <button className={activeSection === "repeat-customers" ? "main-nav-active" : ""} onClick={() => setActiveSection("repeat-customers")}>Repeat Customers</button>
        <button className={activeSection === "inventory-alerts" ? "main-nav-active" : ""} onClick={() => setActiveSection("inventory-alerts")}>Inventory Alerts</button>
        <button className={activeSection === "store-reviews" ? "main-nav-active" : ""} onClick={() => setActiveSection("store-reviews")}>Store Reviews</button>
        <button className={activeSection === "customer-journey" ? "main-nav-active" : ""} onClick={() => setActiveSection("customer-journey")}>Customer Journey</button>
        <div className="sync-nav-control">
          <span>DATABASE ACTION</span>
          <button disabled={syncStatus.status === "running"} onClick={() => void runSync()}>{syncStatus.status === "running" ? <><span className="spinner" /> Sync running…</> : "Run incremental sync"}</button>
        </div>
      </nav>

      <section className="intro" id="top">
        <div>
          <div className="eyebrow">{activeSection === "tables" ? "MYSQL DATABASE" : activeSection === "sku" ? "PRODUCT PERFORMANCE" : activeSection === "store" ? "STORE PERFORMANCE" : activeSection === "inventory" ? "INVENTORY + SALES" : activeSection === "food-type" ? "CATEGORY PERFORMANCE" : activeSection === "hourly" || activeSection === "store-hourly" ? "SALES TIMING" : activeSection === "customers" ? "CUSTOMER PERFORMANCE" : activeSection === "repeat-customers" ? "CUSTOMER MIX" : activeSection === "store-reviews" ? "CUSTOMER FEEDBACK" : activeSection === "customer-journey" ? "CUSTOMER JOURNEY" : "INVENTORY ALERT"}</div>
          <h1>{activeSection === "tables" ? "Tables in MySQL" : activeSection === "sku" ? "Top selling SKUs" : activeSection === "store" ? "Store performance" : activeSection === "inventory" ? "Available stock and total sales" : activeSection === "food-type" ? "Sales by food type" : activeSection === "hourly" ? "Hourly sales by hour" : activeSection === "store-hourly" ? "Sales by store and hour" : activeSection === "customers" ? "Top customers" : activeSection === "repeat-customers" ? "Repeat and one-time customers" : activeSection === "inventory-alerts" ? "Inventory alert tables" : activeSection === "store-reviews" ? "Store Reviews" : activeSection === "customer-journey" ? "Customer Journey" : "High inventory, low sales"}</h1>
          <p>{activeSection === "tables" ? "Browse the tables, columns, and latest rows from your configured MySQL database." : activeSection === "sku" ? "Compare top products by quantity and sales value." : activeSection === "store" ? "Compare store sales, orders, and quantity from MySQL." : activeSection === "inventory" ? "Compare on-hand quantities with the 30-day sales values in MySQL." : activeSection === "food-type" ? "See how sales value is distributed across food types." : activeSection === "hourly" ? "See total sales value by hour; the MySQL hourly table does not include store IDs." : activeSection === "store-hourly" ? "Explore hourly sales patterns for each store in a pivot-style heatmap." : activeSection === "customers" ? "Compare your top customers by order count and sales value." : activeSection === "repeat-customers" ? "See the customer mix from repeat_nonrepeated_customers." : activeSection === "inventory-alerts" ? "Review slow-moving overstock and fast-selling low-stock SKU / store pairs." : activeSection === "store-reviews" ? "Browse customer ratings and comments from all stores." : activeSection === "customer-journey" ? "See the latest customer counts across Cart, Order, and Payment." : "Rank high-stock SKU / store pairs using 30-day sales from MySQL."}</p>
        </div>
        {activeSection !== "customer-journey" ? <div className="freshness">
          <span className="freshness-label">LATEST SALES DATA</span>
          <strong>{prettyDate(metadata?.latest_date ?? null)}</strong>
        </div> : null}
      </section>

      {activeSection === "store-reviews" ? null : activeSection === "customer-journey" ? <section className="filter-card customer-journey-store-filter" aria-label="Customer journey store filter">
        <div className="filter-heading"><div><span className="filter-icon" aria-hidden="true">⌖</span><strong>Store filter</strong></div><span className="filter-hint">Snapshot data · no date field</span></div>
        <div className="filter-controls"><StorePicker stores={metadata?.stores ?? []} value={storeIds} onChange={setStoreIds} /></div>
      </section> : activeSection !== "tables" ? <section className="filter-card" aria-label="Report filters">
        <div className="filter-heading">
          <div><span className="filter-icon" aria-hidden="true">↔</span><strong>Date range</strong></div>
          <span className="filter-hint">Dates are inclusive</span>
        </div>
        <div className="filter-controls">
          <StorePicker stores={metadata?.stores ?? []} value={storeIds} onChange={setStoreIds} />
          <label className="date-field">
            <span>From</span>
            <input type="date" value={startDate} min={metadata?.min_date ?? undefined} max={endDate || metadata?.latest_date || undefined} onChange={(event) => setStartDate(event.target.value)} />
          </label>
          <span className="date-separator">—</span>
          <label className="date-field">
            <span>To</span>
            <input type="date" value={endDate} min={startDate || metadata?.min_date || undefined} max={metadata?.latest_date ?? undefined} onChange={(event) => setEndDate(event.target.value)} />
          </label>
          <div className="preset-group" aria-label="Date presets">
            <button className="preset-button" onClick={applyLastSevenDays} disabled={!metadata?.latest_date}>Last 7 days</button>
            <button className="preset-button" onClick={applyLastMonth} disabled={!metadata?.latest_date}>Last month</button>
            <button className="preset-button" onClick={applyAllTime} disabled={!metadata?.min_date || !metadata.latest_date}>All time</button>
          </div>
          <button className="apply-button" onClick={() => void loadRange(startDate, endDate)} disabled={loading || invalidRange}>
            {loading ? <span className="spinner" /> : null}
            {loading ? "Loading" : "Apply filter"}
            {!loading ? <span aria-hidden="true">→</span> : null}
          </button>
        </div>
        <div className="range-note">Presets use the latest available sales date: {prettyDate(metadata?.latest_date ?? null)}.</div>
      </section> : null}

      {error ? <div className="error-banner" role="alert"><span>!</span>{error}</div> : null}

      {activeSection === "tables" ? <DatabaseTableBrowser tables={databaseTables} selectedTable={selectedTable} onSelect={(name) => { setSelectedTable(name); setTableOffset(0); }} preview={tablePreview} offset={tableOffset} onOffsetChange={setTableOffset} loading={tablesLoading} error={tableError} syncStatus={syncStatus} /> : activeSection === "inventory-alerts" ? <InventoryAlertTables report={inventoryAlertsReport} loading={loading} offset={inventoryAlertOffset} onOffsetChange={(offset) => void changeInventoryAlertPage(offset)} /> : activeSection === "store-reviews" ? <StoreFeedbackReviews refreshKey={storeReviewsRefreshKey} stores={metadata?.stores ?? []} /> : activeSection === "customer-journey" ? <CustomerJourneyPage storeIds={storeIds} refreshKey={customerJourneyRefreshKey} /> : activeSection === "sku" ? <>
      <section className="panel trend-panel">
        <div className="panel-heading trend-heading">
          <div><span className="panel-kicker">{reportView === "chart" ? "QUANTITY + SALES VALUE" : "TOP SKU REPORTS"}</span><h2>{reportView === "chart" ? "Top SKU trends" : "Top SKU tables"}</h2></div>
          <div className="report-view-controls">
          {reportView === "chart" ? <label className="chart-limit-control">
            <span>Show</span>
            <select value={chartLimit} onChange={(event) => {
              const value = Number(event.target.value);
              setChartLimit(value);
              if (startDate && endDate) void loadRange(startDate, endDate, value);
            }}>
              <option value={5}>Top 5</option>
              <option value={10}>Top 10</option>
              <option value={20}>Top 20</option>
            </select>
          </label> : null}
          <div className="view-switch" role="group" aria-label="Report display">
            <button type="button" className={reportView === "chart" ? "view-switch-active" : ""} aria-pressed={reportView === "chart"} onClick={() => setReportView("chart")}>Chart</button>
            <button type="button" className={reportView === "table" ? "view-switch-active" : ""} aria-pressed={reportView === "table"} onClick={() => setReportView("table")}>Table</button>
          </div>
          </div>
        </div>
        {reportView === "chart" ? <>
        {loading && !report ? <div className="chart-empty">Loading sales…</div> : <div className="dual-chart-grid">
          <div className="metric-chart-card"><h3>Top sellers by quantity</h3><SkuLineChart key={(report?.quantity_trend ?? []).map((point) => `${point.sku_id}-${point.store_id}-${point.quantity}`).join("|")} metric="quantity" points={(report?.quantity_trend ?? []).map((point) => ({ ...point, value: point.quantity }))} /></div>
          <div className="metric-chart-card"><h3>Top sellers by sales value</h3><SkuLineChart key={(report?.trend ?? []).map((point) => `${point.sku_id}-${point.store_id}-${point.sales_value}`).join("|")} metric="value" points={(report?.trend ?? []).map((point) => ({ ...point, value: point.sales_value }))} /></div>
        </div>}
        <div className="chart-caption"><span className="chart-caption-dot" /> Hover over a point to see its SKU, store, and value.</div>
        </> : null}
      </section>

      {reportView === "table" ? <section className="dual-table-grid">
        <article className="panel ranking-table-card">
          <div className="table-heading"><div><span className="panel-kicker">GROUPED BY SKU · COUNT</span><h2>top_selling_skus_count</h2></div><span className="row-count">{number(quantityTableRows.length)} SKUs</span></div>
          <div className="table-scroll"><table>
            <thead><tr><th>Rank</th><th>SKU ID</th><th>SKU name</th><th className="numeric">Total quantity</th></tr></thead>
            <tbody>{quantityTableRows.map((row) => <tr key={row.sku_id}>
              <td><span className={`rank-pill ${row.rank <= 3 ? "rank-highlight" : ""}`}>{String(row.rank).padStart(2, "0")}</span></td>
              <td>{row.sku_id}</td>
              <td><span className="table-truncate" title={row.sku_name}>{row.sku_name}</span></td>
              <td className="numeric quantity-cell">{number(row.quantity, 2)}</td>
            </tr>)}</tbody>
          </table>{loading && !report ? <div className="table-empty">Loading…</div> : null}</div>
        </article>
        <article className="panel ranking-table-card">
          <div className="table-heading"><div><span className="panel-kicker">GROUPED BY SKU · VALUE</span><h2>top_selling_skus_value</h2></div><span className="row-count">{number(valueTableRows.length)} SKUs</span></div>
          <div className="table-scroll"><table>
            <thead><tr><th>Rank</th><th>SKU ID</th><th>SKU name</th><th className="numeric">Total sales value</th></tr></thead>
            <tbody>{valueTableRows.map((row) => <tr key={row.sku_id}>
              <td><span className={`rank-pill ${row.rank <= 3 ? "rank-highlight" : ""}`}>{String(row.rank).padStart(2, "0")}</span></td>
              <td>{row.sku_id}</td>
              <td><span className="table-truncate" title={row.sku_name}>{row.sku_name}</span></td>
              <td className="numeric sales-value-cell">{money(row.sales_value)}</td>
            </tr>)}</tbody>
          </table>{loading && !report ? <div className="table-empty">Loading…</div> : null}</div>
        </article>
      </section> : null}
      </> : activeSection === "store" ? <StorePerformanceChart stores={storeReport?.stores ?? []} metric={storeMetric} limit={storeLimit} onMetricChange={setStoreMetric} onLimitChange={setStoreLimit} /> : activeSection === "inventory" ? <InventoryDemandList report={inventoryReport} /> : activeSection === "food-type" ? <FoodTypeSalesChart report={foodTypeReport} /> : activeSection === "hourly" ? <HourlyStoreSalesChart report={hourlyReport} storeIds={storeIds} /> : activeSection === "store-hourly" ? <StoreHourlyPivot report={storeHourlyReport} /> : activeSection === "customers" ? <TopCustomersChart report={customerReport} limit={chartLimit} onLimitChange={(limit) => { setChartLimit(limit); if (startDate && endDate) void loadRange(startDate, endDate, limit); }} /> : activeSection === "repeat-customers" ? <RepeatCustomerSegmentsChart report={customerSegmentsReport} /> : <HighInventoryChart report={highInventoryReport} storeIds={storeIds} />}
      <ChatWidget chartContext={chartContext} />
    </main>
  );
}
