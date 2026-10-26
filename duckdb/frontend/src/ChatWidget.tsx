import { useEffect, useRef, useState, type MouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import "./chat-widget.css";

const CHAT_API = import.meta.env.VITE_CHAT_API_BASE ?? "http://127.0.0.1:8002/api";

type Chat = { id: string; title: string };
type ChatMessage = { role: "user" | "assistant"; content: string; metadata?: ChatResult; pending?: boolean; failed?: boolean; requestId?: string };
type ChatResult = { columns: string[]; rows: Record<string, unknown>[]; row_count: number; execution_time_ms: number; sql: string };
type Health = { generation?: boolean; gemini_generation?: boolean; openrouter_generation?: boolean; kaggle_generation?: boolean; gemini_configured?: boolean; openrouter_configured?: boolean; openrouter_model?: string; model_discovery?: boolean; kaggle_configured?: boolean; kaggle_model_error?: string; model_error?: string; model_errors?: Record<string, string>; database?: boolean; model?: string; kaggle_model?: string; available_models?: string[]; available_kaggle_models?: string[]; available_openrouter_models?: string[]; available_openrouter_free_models?: string[] };
const GEMINI_MODEL_CATALOG = [
  "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts",
  "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest", "gemini-2.5-flash-lite",
  "gemini-2.5-flash-image", "gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-3.1-pro-preview-customtools",
  "gemini-3.1-flash-lite-preview", "gemini-3.1-flash-lite", "gemini-3-pro-image-preview", "gemini-3-pro-image",
  "gemini-3.1-flash-image-preview", "gemini-3.1-flash-image", "gemini-3.1-flash-lite-image", "gemini-nano-banana-2.1",
  "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-omni-flash-preview", "gemini-omni-1.1-flash",
  "gemini-3.5-transcribe", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-4-argon",
  "gemini-3.1-flash-tts-preview", "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-robotics-er-2-preview",
  "gemini-2.5-computer-use-preview-10-2025",
];

function inlineMarkdown(text: string) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    return part;
  });
}

function AssistantText({ text }: { text: string }) {
  return <div className="duck-chat-copy">{text.split("\n").map((line, index) => {
    const listItem = line.match(/^\s*[-*]\s+(.+)$/);
    if (listItem) return <div className="duck-chat-md-listitem" key={index}><span aria-hidden="true">•</span><span>{inlineMarkdown(listItem[1])}</span></div>;
    return line ? <p key={index}>{inlineMarkdown(line)}</p> : <div className="duck-chat-md-spacer" key={index} />;
  })}</div>;
}

function formatResultCell(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);
  }
  return String(value ?? "");
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${CHAT_API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Chat service request failed.");
  }
  return response.json() as Promise<T>;
}

function QueryResult({ data }: { data: ChatResult }) {
  const [showSql, setShowSql] = useState(false);
  return <div className="duck-chat-result">
    <div className="duck-chat-result-heading"><strong>Query results</strong><span>{data.row_count} rows · {data.execution_time_ms} ms</span></div>
    {data.rows.length ? <div className="duck-chat-result-scroll"><table><thead><tr>{data.columns.map((column) => <th key={column} title={column}>{column}</th>)}</tr></thead><tbody>{data.rows.slice(0, 10).map((row, index) => <tr key={index}>{data.columns.map((column) => <td key={column} title={formatResultCell(row[column])}>{formatResultCell(row[column])}</td>)}</tr>)}</tbody></table>{data.rows.length > 10 ? <small>Showing first 10 of {data.rows.length} rows</small> : null}</div> : <p className="duck-chat-no-rows">The query returned no rows.</p>}
    <button className="duck-chat-sql-toggle" onClick={() => setShowSql((value) => !value)}>{showSql ? "Hide SQL" : "View SQL"}</button>
    {showSql ? <pre>{data.sql}</pre> : null}
  </div>;
}

export default function ChatWidget({ chartContext }: { chartContext: string }) {
  const [opened, setOpened] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [chats, setChats] = useState<Chat[]>([]);
  const [active, setActive] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [model, setModel] = useState("");
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [modelSearch, setModelSearch] = useState("");
  const [expandedModelGroup, setExpandedModelGroup] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [tables, setTables] = useState<{ name: string; columns: { name: string; type: string }[] }[]>([]);
  const [schemaOpen, setSchemaOpen] = useState(false);
  const lock = useRef(false);
  const messagesRef = useRef<HTMLElement>(null);
  const widgetRef = useRef<HTMLElement>(null);
  const dragRef = useRef<{ pointerId: number; x: number; y: number; left: number; top: number } | null>(null);
  const [widgetPosition, setWidgetPosition] = useState<{ left: number; top: number } | null>(null);
  const availableGeminiModels = health?.gemini_configured
    ? [...new Set([...(health.available_models ?? []), ...GEMINI_MODEL_CATALOG])]
    : (health?.available_models ?? []);

  function startDragging(event: ReactPointerEvent<HTMLElement>) {
    if ((event.target as HTMLElement).closest("button, select, input, textarea")) return;
    const widget = widgetRef.current;
    if (!widget) return;
    const rect = widget.getBoundingClientRect();
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  function moveWidget(event: ReactPointerEvent<HTMLElement>) {
    const drag = dragRef.current;
    const widget = widgetRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !widget) return;
    setWidgetPosition({
      left: Math.max(0, Math.min(window.innerWidth - widget.offsetWidth, drag.left + event.clientX - drag.x)),
      top: Math.max(0, Math.min(window.innerHeight - widget.offsetHeight, drag.top + event.clientY - drag.y)),
    });
  }

  function stopDragging(event: ReactPointerEvent<HTMLElement>) {
    if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null;
  }

  async function loadSidebar() {
    const conversationsTask = request<{ conversations: Chat[] }>("/conversations")
      .then((data) => setChats(data.conversations))
      .catch(() => undefined);
    const healthTask = request<Health>("/health").then((status) => {
      setHealth(status);
      setModel((current) => {
        // The fast health endpoint intentionally omits dynamic provider catalogs.
        // Preserve a user-selected model while /models reloads after a reply.
        if (current.startsWith("openrouter::") || current.startsWith("kaggle::")) return current;
        const gemini = (status.available_models ?? []).map((name) => `gemini::${name}`);
        const kaggle = (status.available_kaggle_models ?? []).map((name) => `kaggle::${name}`);
        const openrouter = [...(status.available_openrouter_free_models ?? []), ...(status.available_openrouter_models ?? [])].map((name) => `openrouter::${name}`);
        if (current && [...gemini, ...kaggle, ...openrouter].includes(current)) return current;
        if (status.model && gemini.includes(`gemini::${status.model}`)) return `gemini::${status.model}`;
        if (status.kaggle_model && kaggle.includes(`kaggle::${status.kaggle_model}`)) return `kaggle::${status.kaggle_model}`;
        if (status.openrouter_model && openrouter.includes(`openrouter::${status.openrouter_model}`)) return `openrouter::${status.openrouter_model}`;
        return gemini[0] ?? kaggle[0] ?? openrouter[0] ?? "";
      });
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "Cannot connect to the chat service.");
    });
    const modelsTask = request<Health>("/models").then((catalog) => {
      setHealth((current) => ({ ...current, ...catalog }));
      if (catalog.model_errors?.openrouter) setError(`Could not load OpenRouter models: ${catalog.model_errors.openrouter}`);
      setModel((current) => {
        const gemini = (catalog.available_models ?? []).map((name) => `gemini::${name}`);
        const kaggle = (catalog.available_kaggle_models ?? []).map((name) => `kaggle::${name}`);
        const openrouter = [...(catalog.available_openrouter_free_models ?? []), ...(catalog.available_openrouter_models ?? [])].map((name) => `openrouter::${name}`);
        if (current && [...gemini, ...kaggle, ...openrouter].includes(current)) return current;
        if (catalog.openrouter_model && openrouter.includes(`openrouter::${catalog.openrouter_model}`)) return `openrouter::${catalog.openrouter_model}`;
        return gemini[0] ?? kaggle[0] ?? openrouter[0] ?? current;
      });
    }).catch(() => undefined);
    const schemaTask = request<{ tables: typeof tables }>("/database/schema")
      .then((data) => setTables(data.tables))
      .catch(() => undefined);
    await Promise.allSettled([conversationsTask, healthTask, modelsTask, schemaTask]);
  }

  useEffect(() => { void loadSidebar(); }, []);
  useEffect(() => { if (messagesRef.current) messagesRef.current.scrollTop = messagesRef.current.scrollHeight; }, [messages]);

  async function openChat(id: string) {
    setActive(id); setError(""); setHistoryOpen(false);
    try { setMessages((await request<{ messages: ChatMessage[] }>(`/conversations/${id}/messages`)).messages); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not load this conversation."); }
  }

  async function newChat() {
    try {
      const conversation = await request<{ id: string }>("/conversations", { method: "POST", body: "{}" });
      setActive(conversation.id); setMessages([]); setError(""); setHistoryOpen(false); await loadSidebar();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not start a conversation."); }
  }

  async function deleteChat(id: string, event: MouseEvent<HTMLButtonElement>) {
    event.stopPropagation();
    try {
      await request(`/conversations/${id}`, { method: "DELETE" });
      if (active === id) { setActive(""); setMessages([]); }
      await loadSidebar();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not delete this conversation."); }
  }

  async function send(text = input.trim(), retryMessage?: ChatMessage) {
    if (!text || lock.current) return;
    lock.current = true; setBusy(true); setError("");
    let conversationId = active;
    if (!conversationId) {
      try {
        const conversation = await request<{ id: string }>("/conversations", { method: "POST", body: "{}" });
        conversationId = conversation.id; setActive(conversationId);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not start a conversation."); lock.current = false; setBusy(false); return;
      }
    }
    const requestId = retryMessage?.requestId ?? crypto.randomUUID();
    if (retryMessage) setMessages((items) => items.map((item) => item.requestId === requestId ? { ...item, failed: false } : item));
    else { setMessages((items) => [...items, { role: "user", content: text, requestId }]); setInput(""); }
    setMessages((items) => [...items, { role: "assistant", content: "", pending: true, requestId }]);
    try {
      const result = await request<{ reply: string; sql?: string; rows?: Record<string, unknown>[]; columns?: string[]; row_count?: number; execution_time_ms?: number }>("/chat", {
        method: "POST", body: JSON.stringify({ conversation_id: conversationId, message: text, model: model || undefined, chart_context: chartContext || undefined }),
      });
      if (!result.reply?.trim()) throw new Error("The model returned an empty reply. Try again or select another model.");
      setMessages((items) => items.map((item) => item.requestId === requestId && item.role === "assistant" ? {
        role: "assistant", content: result.reply,
        metadata: result.sql ? { sql: result.sql, rows: result.rows ?? [], columns: result.columns ?? [], row_count: result.row_count ?? 0, execution_time_ms: result.execution_time_ms ?? 0 } : undefined,
        requestId,
      } : item));
      await loadSidebar();
    } catch (err) {
      setMessages((items) => items.filter((item) => !(item.requestId === requestId && item.role === "assistant" && item.pending)).map((item) => item.requestId === requestId && item.role === "user" ? { ...item, failed: true } : item));
      setError(err instanceof Error ? err.message : "Could not send your message.");
    } finally { lock.current = false; setBusy(false); }
  }

  const kaggleStatus = health?.available_kaggle_models?.length
    ? `${health.available_kaggle_models.length} Kaggle models ready`
    : health?.kaggle_model_error ? "Kaggle proxy unavailable" : "No Kaggle models listed";
  const selectedProviderStatus = model.startsWith("kaggle::")
    ? health?.kaggle_generation ? "Kaggle proxy online" : "Kaggle proxy unavailable"
    : model.startsWith("openrouter::")
      ? health?.openrouter_generation ? "OpenRouter online" : health?.openrouter_configured ? "OpenRouter configured" : "OpenRouter unavailable"
      : health?.gemini_generation ? "Gemini online" : health?.gemini_configured ? health.model_discovery ? "Gemini unavailable" : "Gemini configured" : health?.kaggle_generation ? "Kaggle proxy online" : "Select an available model";
  const hasModels = Boolean(availableGeminiModels.length || health?.available_kaggle_models?.length || health?.available_openrouter_models?.length || health?.available_openrouter_free_models?.length);
  const selectedModelLabel = model.replace(/^(gemini|kaggle|openrouter)::/, "");
  const matchesModelSearch = (name: string) => name.toLowerCase().includes(modelSearch.trim().toLowerCase());
  const modelGroups = [
    { id: "gemini", title: "Gemini models", provider: "gemini", models: availableGeminiModels },
    { id: "openrouter-free", title: "OpenRouter free models", provider: "openrouter", models: health?.available_openrouter_free_models ?? [] },
    { id: "openrouter-paid", title: "OpenRouter paid models", provider: "openrouter", models: health?.available_openrouter_models ?? [] },
    { id: "kaggle", title: "Kaggle proxy models", provider: "kaggle", models: health?.available_kaggle_models ?? [] },
  ].map((group) => ({ ...group, models: group.models.filter(matchesModelSearch) })).filter((group) => group.models.length);

  return <>
    {!opened ? <button className="duck-chat-launcher" aria-label="Open StoreNxt analytics assistant" onClick={() => setOpened(true)}><span aria-hidden="true">✦</span><i /></button> : null}
    {opened ? <section ref={widgetRef} className={`duck-chat-widget${historyOpen ? " history-open" : ""}`} style={widgetPosition ? { left: widgetPosition.left, top: widgetPosition.top, right: "auto", bottom: "auto" } : undefined} aria-label="StoreNxt Assistant">
      <aside className="duck-chat-sidebar">
        <button className="duck-chat-sidebar-back" aria-label="Back to chat" title="Back to chat" onClick={() => setHistoryOpen(false)}>Back</button>
        <div className="duck-chat-brand"><span>✦</span><div><strong>StoreNxt Assistant</strong><small>BUSINESS ANALYTICS</small></div></div>
        <button className="duck-chat-new" onClick={() => void newChat()}>＋ <span>New conversation</span></button>
        <div className="duck-chat-sidebar-label">RECENT CHATS</div>
        <div className="duck-chat-history">{chats.map((chat) => <div key={chat.id} className={`duck-chat-item${active === chat.id ? " selected" : ""}`} onClick={() => void openChat(chat.id)}><span>{chat.title}</span><button aria-label="Delete conversation" onClick={(event) => void deleteChat(chat.id, event)}>×</button></div>)}{!chats.length ? <small>Your conversations will appear here</small> : null}</div>
        <div className="duck-chat-sidebottom">
          <button className="duck-chat-schema-toggle" onClick={() => setSchemaOpen((value) => !value)}>▦ <span>Database tables</span><span>{schemaOpen ? "−" : "+"}</span></button>
          {schemaOpen ? <div className="duck-chat-table-list">{tables.map((table) => <div key={table.name} title={table.columns.map((column) => `${column.name}: ${column.type}`).join(", ")}>{table.name}</div>)}</div> : null}
          <small><i className={health?.database ? "online" : ""} />Database {health?.database ? "connected" : "unavailable"}</small>
          <small title={health?.model_error}><i className={health?.gemini_generation ? "online" : ""} />Gemini {health?.gemini_generation ? "ready" : health?.gemini_configured ? health.model_discovery ? "unavailable" : "configured" : "not configured"}</small>
          <small><i className={health?.openrouter_generation ? "online" : ""} />OpenRouter {health?.openrouter_configured ? `${(health.available_openrouter_models?.length ?? 0) + (health.available_openrouter_free_models?.length ?? 0)} models ready` : "not configured"}</small>
          <small><i className={health?.kaggle_generation ? "online" : ""} />{kaggleStatus}</small>
        </div>
      </aside>
      <div className="duck-chat-main">
        <header className="duck-chat-header" onPointerDown={startDragging} onPointerMove={moveWidget} onPointerUp={stopDragging} onPointerCancel={stopDragging}>
          <button aria-label="Conversation history" onClick={() => setHistoryOpen((value) => !value)}>☰</button><span className="duck-chat-botmark">✦</span>
          <div className="duck-chat-title"><strong>StoreNxt Assistant</strong><small><i className={model.startsWith("kaggle::") ? health?.kaggle_generation ? "online" : "" : health?.gemini_generation ? "online" : ""} />{selectedProviderStatus}</small></div>
          <div className="duck-chat-model-picker">
            <button className="duck-chat-model-trigger" aria-label="Choose AI model" aria-expanded={modelMenuOpen} title={selectedModelLabel} disabled={!hasModels || busy} onClick={() => setModelMenuOpen((open) => !open)}>{selectedModelLabel || "No models"}<span aria-hidden="true">⌄</span></button>
            {modelMenuOpen ? <div className="duck-chat-model-menu" aria-label="Available AI models">
              <input className="duck-chat-model-search" aria-label="Search AI models" placeholder="Search models…" value={modelSearch} onChange={(event) => setModelSearch(event.target.value)} />
              {modelGroups.map((group) => <div className="duck-chat-model-category" key={group.id}>
                <button className="duck-chat-model-category-trigger" aria-expanded={expandedModelGroup === group.id} onClick={() => setExpandedModelGroup((current) => current === group.id ? null : group.id)}>
                  <span>{group.title} <small>{group.models.length}</small></span><span aria-hidden="true">{expandedModelGroup === group.id ? "?" : "+"}</span>
                </button>
                {expandedModelGroup === group.id ? <div className="duck-chat-model-options">{group.models.map((item) => <button aria-pressed={model === `${group.provider}::${item}`} key={`${group.id}-${item}`} onClick={() => { setModel(`${group.provider}::${item}`); setModelSearch(""); setModelMenuOpen(false); }}>{item}</button>)}</div> : null}
              </div>)}
              {hasModels && !modelGroups.length ? <div className="duck-chat-model-empty">No matching models</div> : null}
              {!hasModels ? <div className="duck-chat-model-empty">No models available</div> : null}
            </div> : null}
          </div>
          <button aria-label="New conversation" onClick={() => void newChat()}>＋</button><button aria-label="Close chat" onClick={() => setOpened(false)}>×</button>
        </header>
        <section className="duck-chat-messages" ref={messagesRef}>
          {!messages.length ? <div className="duck-chat-welcome"><div className="duck-chat-welcome-icon">✦</div><h2>Hi, how can I help?</h2><p>{chartContext ? "Ask about the dashboard report currently on screen, including its date and store filters." : "Ask me a question about your StoreNxt business data."}</p><div className="duck-chat-suggestions"><button onClick={() => setInput(chartContext ? "Summarize this chart." : "What tables and views are available?")}>{chartContext ? "Summarize this chart" : "What data is available?"} <span>›</span></button><button onClick={() => setInput(chartContext ? "What does this chart show for the selected filters?" : "Show me the schema for fact_orders")}>{chartContext ? "Explain the selected filters" : "Show an order table schema"} <span>›</span></button><button onClick={() => setInput("Summarize the sales data")}>Summarize the sales data <span>›</span></button></div></div> : <div className="duck-chat-thread">{messages.map((message, index) => <div key={`${message.requestId ?? index}-${message.role}`} className={`duck-chat-message ${message.role}`}>{message.role === "assistant" ? <span className="duck-chat-avatar">✦</span> : null}<div className="duck-chat-bubble">{message.pending ? <span className="duck-chat-thinking">◌ Thinking…</span> : <>{message.role === "assistant" ? <AssistantText text={message.content} /> : <div className="duck-chat-copy">{message.content}</div>}{message.failed ? <button className="duck-chat-retry" onClick={() => void send(message.content, message)}>Retry</button> : null}{message.metadata ? <QueryResult data={message.metadata} /> : null}</>}</div></div>)}</div>}
        </section>
        {error ? <div className="duck-chat-error" role="alert">{error}<button onClick={() => setError("")}>Dismiss</button></div> : null}
        <footer className="duck-chat-footer"><div className="duck-chat-input"><textarea value={input} placeholder={chartContext ? "Ask about this report…" : "Type your message…"} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }} /><button aria-label="Send message" disabled={busy || !input.trim()} onClick={() => void send()}>↑</button></div><small>AI can make mistakes. Check important numbers against the source.</small></footer>
      </div>
    </section> : null}
  </>;
}
