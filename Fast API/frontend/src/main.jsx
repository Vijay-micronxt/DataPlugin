import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Activity, Bot, ChevronDown, ChevronRight, Database, History, LoaderCircle, MessageCircle, Plus, Send, Table2, Trash2, X } from 'lucide-react';
import './style.css';
import './widget.css';
import './model.css';
import './drag.css';

const API = 'http://127.0.0.1:8000/api';
async function req(path, options) {
  const response = await fetch(API + path, { headers: { 'Content-Type': 'application/json' }, ...options });
  if (!response.ok) {
    let message = 'Request failed';
    try { message = (await response.json()).detail || message; } catch { /* keep default */ }
    throw Error(message);
  }
  return response.json();
}

function App() {
  const [opened, setOpened] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [chats, setChats] = useState([]), [active, setActive] = useState(''), [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState(''), [busy, setBusy] = useState(false), [health, setHealth] = useState(null);
  const [selectedModel, setSelectedModel] = useState('');
  const requestLock = useRef(false);
  const dragStart = useRef(null);
  const [widgetPosition, setWidgetPosition] = useState(null);
  const [tables, setTables] = useState([]), [explore, setExplore] = useState(false), [err, setErr] = useState('');

  async function load() {
    try {
      const [conversations, status, schema] = await Promise.all([req('/conversations'), req('/health'), req('/database/schema')]);
      setChats(conversations.conversations); setHealth(status); setTables(schema.tables);
      setSelectedModel(current => current && status.available_models?.includes(current) ? current : status.model || status.available_models?.[0] || '');
    } catch (error) { setErr(error.message); }
  }
  useEffect(() => { load(); }, []);
  async function openChat(id) {
    setActive(id); setErr(''); setShowHistory(false);
    try { setMsgs((await req(`/conversations/${id}/messages`)).messages); }
    catch (error) { setErr(error.message); }
  }
  async function fresh() {
    try {
      const conversation = await req('/conversations', { method: 'POST', body: JSON.stringify({}) });
      await load(); setActive(conversation.id); setMsgs([]); setErr(''); setShowHistory(false);
    } catch (error) { setErr(error.message); }
  }
  async function requestMessage(id, text, requestId, retrying = false, lockHeld = false) {
    if (requestLock.current && !lockHeld) return;
    if (!lockHeld) requestLock.current = true;
    setBusy(true); setErr('');
    setMsgs(messages => {
      const next = retrying ? [...messages] : [...messages, { role: 'user', content: text, requestId }];
      next.push({ role: 'assistant', content: '', pending: true, requestId });
      return next;
    });
    try {
      const data = await req('/chat', { method: 'POST', body: JSON.stringify({ conversation_id: id, message: text, model: selectedModel || undefined }) });
      if (!data.reply?.trim()) throw Error('The model returned an empty reply. Try again or choose another model.');
      setMsgs(messages => messages.map(message => message.requestId === requestId && message.role === 'assistant'
        ? { role: 'assistant', content: data.reply, metadata: data.sql ? data : null, requestId }
        : message));
      const conversations = await req('/conversations'); setChats(conversations.conversations);
    } catch (error) {
      setMsgs(messages => messages.filter(message => !(message.requestId === requestId && message.role === 'assistant' && message.pending))
        .map(message => message.requestId === requestId && message.role === 'user' ? { ...message, failed: true } : message));
      setErr(error.message);
    } finally { requestLock.current = false; setBusy(false); }
  }
  async function send() {
    const text = input.trim();
    if (!text || requestLock.current) return;
    requestLock.current = true; setBusy(true);
    let id = active;
    if (!id) {
      try { const conversation = await req('/conversations', { method: 'POST', body: '{}' }); id = conversation.id; setActive(id); await load(); }
      catch (error) { requestLock.current = false; setBusy(false); setErr(error.message); return; }
    }
    const requestId = crypto.randomUUID();
    setInput(''); await requestMessage(id, text, requestId, false, true);
  }
  async function retry(message) {
    if (requestLock.current) return;
    setMsgs(messages => messages.map(item => item.requestId === message.requestId ? { ...item, failed: false } : item));
    await requestMessage(active, message.content, message.requestId, true);
  }
  async function remove(id, event) {
    event.stopPropagation();
    try { await req(`/conversations/${id}`, { method: 'DELETE' }); if (active === id) { setActive(''); setMsgs([]); } await load(); }
    catch (error) { setErr(error.message); }
  }

  function startWidgetDrag(event) {
    if (event.target.closest('button, select, input, textarea')) return;
    const widget = event.currentTarget.closest('.chat-widget');
    const rect = widget.getBoundingClientRect();
    dragStart.current = { pointerX: event.clientX, pointerY: event.clientY, left: rect.left, top: rect.top };
    setWidgetPosition({ left: rect.left, top: rect.top });
    event.preventDefault();
  }
  function moveWidget(event) {
    if (!dragStart.current) return;
    const { pointerX, pointerY, left, top } = dragStart.current;
    const width = document.querySelector('.chat-widget')?.offsetWidth || 400;
    const height = document.querySelector('.chat-widget')?.offsetHeight || 600;
    setWidgetPosition({
      left: Math.max(0, Math.min(window.innerWidth - width, left + event.clientX - pointerX)),
      top: Math.max(0, Math.min(window.innerHeight - height, top + event.clientY - pointerY)),
    });
  }

  return <>
    {!opened && <button className="chat-launcher" aria-label="Open analytics chat" onClick={() => setOpened(true)}>
      <MessageCircle size={25}/><span className="launcher-dot"/>
    </button>}
    {opened && <div className={'chat-widget ' + (showHistory ? 'history-open' : '')} style={widgetPosition ? { left: widgetPosition.left, top: widgetPosition.top, right: 'auto', bottom: 'auto' } : undefined} onPointerMove={moveWidget} onPointerUp={() => { dragStart.current = null; }} onPointerCancel={() => { dragStart.current = null; }}>
      <aside>
        <div className="brand"><span className="logo"><Database size={18}/></span><div><b>StoreNxt Assistant</b><small>BUSINESS ANALYTICS</small></div></div>
        <button className="new" onClick={fresh}><Plus size={16}/> New conversation</button>
        <div className="label">RECENT CHATS</div>
        <div className="history">{chats.map(chat => <div key={chat.id} className={'chatitem ' + (active === chat.id ? 'selected' : '')} onClick={() => openChat(chat.id)}>
          <span>{chat.title}</span><button aria-label="Delete conversation" onClick={event => remove(chat.id, event)}><Trash2 size={14}/></button>
        </div>)}{!chats.length && <div className="muted tiny">Your conversations will appear here</div>}</div>
        <div className="sidebottom">
          <button className="explorebtn" onClick={() => setExplore(!explore)}><Table2 size={15}/> Database tables {explore ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}</button>
          {explore && <div className="tablelist">{tables.map(table => <div key={table.name} title={table.columns.map(col => `${col.name}: ${col.type}`).join(', ')}>{table.name}</div>)}</div>}
          <div className="status"><i className={health?.database ? 'ok' : ''}/><span>Database {health?.database ? 'connected' : 'unavailable'}</span></div>
          <div className="status"><i className={health?.generation ? 'ok' : ''}/><span>{health?.generation ? 'Gemini ready' : health?.gemini_configured ? 'Gemini unavailable' : 'Add Gemini API key'}</span></div>
        </div>
      </aside>
      <main>
        <header onPointerDown={startWidgetDrag}>
          <button className="menu" aria-label="Conversation history" onClick={() => setShowHistory(!showHistory)}><History size={17}/></button>
          <span className="botmark"><Bot size={18}/></span><div className="widget-title"><b>StoreNxt Assistant</b><small><i className={health?.generation ? 'online' : ''}/> {health?.generation ? 'Gemini online' : health?.gemini_configured ? 'Gemini unavailable' : 'Gemini setup needed'}</small></div>
          <select className="model-select" aria-label="Choose AI model" title="Responses will use this model" value={selectedModel} disabled={!health?.available_models?.length || busy} onChange={event => setSelectedModel(event.target.value)}>
            {health?.available_models?.length ? health.available_models.map(model => <option key={model} value={model}>{model}</option>) : <option value="">Model unavailable</option>}
          </select>
          <button className="widget-new" aria-label="New conversation" onClick={fresh}><Plus size={17}/></button>
          <button className="widget-close" aria-label="Close chat" onClick={() => setOpened(false)}><X size={18}/></button>
        </header>
        <section className="messages">
          {!msgs.length ? <div className="welcome">
            <div className="welicon"><Bot size={23}/></div><h1>Hi, how can I help?</h1>
            <p>Ask me a question about your StoreNxt business data.</p>
            <div className="suggestions">
              <button onClick={() => setInput('What tables and views are available?')}>What data is available? <span>›</span></button>
              <button onClick={() => setInput('Show me the schema for fact_orders')}>Show an order table schema <span>›</span></button>
              <button onClick={() => setInput('Summarize the sales data')}>Summarize the sales data <span>›</span></button>
            </div>
          </div> : <div className="thread">{msgs.map((message, index) => <div key={index} className={'message ' + message.role}>
            {message.role === 'assistant' && <div className="avatar"><Bot size={15}/></div>}
            <div className="bubble">{message.pending ? <div className="thinking"><LoaderCircle className="spin" size={15}/> Thinking…</div> : <>
              {message.content ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown> : <span className="empty-reply">No response text was returned.</span>}
              {message.failed && <button className="retry-button" onClick={() => retry(message)}>Retry</button>}
              {message.metadata && <Result data={message.metadata}/>}
            </>}</div>
          </div>)}</div>}
        </section>
        {err && <div className="error">{err}<button onClick={() => setErr('')}>Dismiss</button></div>}
        <footer><div className="inputbox"><textarea value={input} placeholder="Type your message…" onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(); } }}/>
          <button aria-label="Send message" disabled={busy || !input.trim()} onClick={send}><Send size={16}/></button></div>
          <small>AI can make mistakes. Check important numbers against the source.</small></footer>
      </main>
    </div>}
  </>;
}

function Result({ data }) {
  const [showSql, setShowSql] = useState(false), [page, setPage] = useState(0);
  const pageSize = 10, rows = data.rows || [], pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const visible = rows.slice(page * pageSize, (page + 1) * pageSize);
  return <div className="result">
    <div className="result-heading"><b>Query results</b><span>{data.row_count} rows · {data.execution_time_ms} ms</span></div>
    {rows.length ? <><div className="scroll"><table><thead><tr>{data.columns.map(col => <th key={col}>{col}</th>)}</tr></thead><tbody>{visible.map((row, index) => <tr key={index}>{data.columns.map(col => <td key={col}>{String(row[col] ?? '')}</td>)}</tr>)}</tbody></table></div>
      {pageCount > 1 && <div className="pagination"><span>Rows {page * pageSize + 1}–{Math.min((page + 1) * pageSize, rows.length)} of {rows.length}</span><div><button disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><button disabled={page + 1 >= pageCount} onClick={() => setPage(page + 1)}>Next</button></div></div>}
    </> : <div className="empty-results">The query returned no rows.</div>}
    <button className="sqltoggle" onClick={() => setShowSql(!showSql)}><ChevronDown size={14}/>{showSql ? 'Hide SQL' : 'View SQL'}</button>
    {showSql && <pre>{data.sql}</pre>}
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
