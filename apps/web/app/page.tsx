"use client";

import { FormEvent, useState } from "react";


type Message = {
  id: number;
  role: "user" | "assistant";
  content: string;
};


const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";


export default function Home() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 1,
      role: "assistant",
      content: "在。你今天想从哪件事说起？",
    },
  ]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = input.trim();

    if (!message || isSending) {
      return;
    }

    const userMessage: Message = {
      id: Date.now(),
      role: "user",
      content: message,
    };

    setMessages((current) => [...current, userMessage]);
    setInput("");
    setError("");
    setIsSending(true);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({message}),
      });

      if (!response.ok) {
        throw new Error("聊天服务暂时没有回应");
      }

      const data: {reply: string} = await response.json();
      setMessages((current) => [
        ...current,
        {
          id: Date.now() + 1,
          role: "assistant",
          content: data.reply,
        },
      ]);
    } catch {
      setError("无法连接聊天服务。请确认后端已经启动。");
    } finally {
      setIsSending(false);
    }
  }

  return (
    <main className="workspace">
      <aside className="persona-panel">
        <div className="brand">
          <span className="brand-mark">见字</span>
          <span className="brand-note">Persona archive</span>
        </div>

        <section className="persona-card">
          <div className="avatar" aria-hidden="true">林</div>
          <p className="eyebrow">当前人格</p>
          <h1>小林</h1>
          <p className="relationship">与你的老朋友 · 草稿人格</p>
          <div className="trait-list">
            <span>回复简短</span>
            <span>不爱说教</span>
            <span>先听后建议</span>
          </div>
        </section>

        <nav className="side-nav" aria-label="人格管理">
          <button className="active" type="button">对话</button>
          <button type="button">人格画像</button>
          <button type="button">共同记忆</button>
          <button type="button">版本记录</button>
        </nav>

        <p className="disclosure">
          这是基于聊天记录生成的 AI 模拟人格，不是现实人物本人。
        </p>
      </aside>

      <section className="chat-panel">
        <header className="chat-header">
          <div>
            <p className="eyebrow">Conversation 001</p>
            <h2>今天，也从一句话开始</h2>
          </div>
          <span className="status"><i /> 本地原型</span>
        </header>

        <div className="message-list" aria-live="polite">
          <div className="date-divider"><span>今天</span></div>
          {messages.map((message) => (
            <article
              className={`message ${message.role}`}
              key={message.id}
            >
              <span className="message-role">
                {message.role === "user" ? "你" : "小林"}
              </span>
              <p>{message.content}</p>
              {message.role === "assistant" && (
                <div className="feedback" aria-label="评价回复">
                  <button type="button">像</button>
                  <button type="button">不像</button>
                </div>
              )}
            </article>
          ))}
          {isSending && (
            <article className="message assistant pending">
              <span className="message-role">小林</span>
              <p>正在想……</p>
            </article>
          )}
        </div>

        <form className="composer" onSubmit={sendMessage}>
          <label htmlFor="chat-input">想说的话</label>
          <div className="composer-row">
            <textarea
              id="chat-input"
              maxLength={2000}
              onChange={(event) => setInput(event.target.value)}
              placeholder="比如：明天要面试了，有点慌"
              rows={2}
              value={input}
            />
            <button disabled={!input.trim() || isSending} type="submit">
              发送
            </button>
          </div>
          <div className="composer-meta">
            <span className={error ? "error" : ""}>
              {error || "这一阶段先验证消息能否走通"}
            </span>
            <span>{input.length} / 2000</span>
          </div>
        </form>
      </section>

      <aside className="memory-panel">
        <header>
          <p className="eyebrow">Memory spine</p>
          <h2>记忆脉络</h2>
        </header>
        <div className="memory-spine">
          <article>
            <time>初次见面</time>
            <p>人格尚未从真实聊天记录构建。</p>
          </article>
          <article>
            <time>当前阶段</time>
            <p>Web 页面已经接通最小聊天 API。</p>
          </article>
          <article className="muted">
            <time>下一步</time>
            <p>导入记录后，这里会出现双方真正的共同经历。</p>
          </article>
        </div>
      </aside>
    </main>
  );
}
