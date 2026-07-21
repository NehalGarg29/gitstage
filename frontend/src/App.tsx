import React, { useState, useEffect, useRef } from "react";
import { 
  FolderGit2, 
  MessageSquare, 
  Code2, 
  Cpu, 
  Plus, 
  RefreshCw, 
  CheckCircle2, 
  AlertCircle, 
  Send,
  Loader2,
  FileCode2,
  Server,
  Sparkles,
  Lock,
  CreditCard,
  Check
} from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { atomDark } from "react-syntax-highlighter/dist/esm/styles/prism";

interface Repository {
  id: number;
  name: string;
  full_name: string;
  status: string;
  error_message: string | null;
  files_count?: number;
  chunks_count?: number;
  created_at: string;
}

interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
}

interface Source {
  filepath: string;
  name: string;
  type: string;
  start_line: number;
  end_line: number;
  code_content: string;
}

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8080";
const WS_URL = API_URL.replace("http://", "ws://").replace("https://", "wss://");

export default function App() {
  // Simple Path-based router
  const [currentPath, setCurrentPath] = useState(window.location.pathname);

  useEffect(() => {
    const handleLocationChange = () => {
      setCurrentPath(window.location.pathname);
    };
    window.addEventListener("popstate", handleLocationChange);
    return () => window.removeEventListener("popstate", handleLocationChange);
  }, []);

  // If path is the mock checkout route, show the Stripe sandbox page
  if (currentPath === "/payments/mock-checkout") {
    return <MockCheckoutPage navigateTo={(path) => {
      window.history.pushState({}, "", path);
      setCurrentPath(path);
    }} />;
  }

  return <DashboardPage navigateTo={(path) => {
    window.history.pushState({}, "", path);
    setCurrentPath(path);
  }} />;
}

// ==================== DASHBOARD PAGE ====================
function DashboardPage({ navigateTo }: { navigateTo: (path: string) => void }) {
  const [repos, setRepos] = useState<Repository[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState<number | null>(null);
  const [currentRepo, setCurrentRepo] = useState<Repository | null>(null);
  const [showConnectModal, setShowConnectModal] = useState(false);
  
  // User Profile / Tier State
  const [userTier, setUserTier] = useState<"free" | "pro">("free");
  const [username, setUsername] = useState("localdev");

  // Connection Form State
  const [repoName, setRepoName] = useState("");
  const [ownerUsername, setOwnerUsername] = useState("localdev");
  const [localPath, setLocalPath] = useState("/app");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Chat State
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      content: "👋 Hello! I am GitStage. Connect a Python repository, and ask me questions about its structure, functions, classes, or architecture."
    }
  ]);
  const [query, setQuery] = useState("");
  const [isChatting, setIsChatting] = useState(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [activeSource, setActiveSource] = useState<Source | null>(null);

  // Ingestion progress state
  const [syncProgress, setSyncProgress] = useState<{
    progress: number;
    message: string;
    status: string;
  } | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  // Fetch User Profile
  const fetchUserProfile = async () => {
    try {
      const res = await fetch(`${API_URL}/auth/me`);
      if (res.ok) {
        const data = await res.json();
        setUserTier(data.subscription_tier);
        setUsername(data.username);
      }
    } catch (err) {
      console.error("Error fetching user profile:", err);
    }
  };

  // Fetch repositories
  const fetchRepos = async () => {
    try {
      const res = await fetch(`${API_URL}/repos`);
      if (res.ok) {
        const data = await res.json();
        setRepos(data);
        if (data.length > 0 && selectedRepoId === null) {
          setSelectedRepoId(data[0].id);
        }
      }
    } catch (err) {
      console.error("Error fetching repos:", err);
    }
  };

  // Fetch detailed repository info
  const fetchRepoDetails = async (id: number) => {
    try {
      const res = await fetch(`${API_URL}/repos/${id}`);
      if (res.ok) {
        const data = await res.json();
        setCurrentRepo(data);
        
        if (data.status === "indexing") {
          connectWebSocket(id);
        } else {
          setSyncProgress(null);
        }
      }
    } catch (err) {
      console.error("Error fetching repo details:", err);
    }
  };

  // Connect WebSocket progress
  const connectWebSocket = (repoId: number) => {
    if (socketRef.current) {
      socketRef.current.close();
    }

    const ws = new WebSocket(`${WS_URL}/ws/progress/${repoId}`);
    socketRef.current = ws;

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.ping) return;
      
      setSyncProgress({
        progress: data.progress,
        message: data.message,
        status: data.status
      });

      if (data.status === "synced" || data.status === "failed") {
        fetchRepos();
        if (selectedRepoId === repoId) {
          fetchRepoDetails(repoId);
        }
        ws.close();
      }
    };
  };

  useEffect(() => {
    fetchUserProfile();
    fetchRepos();
    const interval = setInterval(() => {
      fetchRepos();
      fetchUserProfile();
    }, 10000);
    return () => {
      clearInterval(interval);
      if (socketRef.current) socketRef.current.close();
    };
  }, []);

  useEffect(() => {
    if (selectedRepoId !== null) {
      fetchRepoDetails(selectedRepoId);
      setMessages([
        {
          id: "welcome-repo",
          role: "assistant",
          content: "I've loaded the repository context. Ask me anything about the codebase (e.g. 'What endpoints are available?' or 'How is the database initialized?')"
        }
      ]);
      setSources([]);
      setActiveSource(null);
    }
  }, [selectedRepoId]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleConnectRepo = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/repos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: repoName,
          owner_username: ownerUsername,
          local_path: localPath
        })
      });
      
      if (res.ok) {
        const data = await res.json();
        setShowConnectModal(false);
        setRepoName("");
        await fetchRepos();
        setSelectedRepoId(data.repository_id);
        connectWebSocket(data.repository_id);
      } else {
        const errData = await res.json();
        alert(errData.detail || "Failed to start repository ingestion.");
      }
    } catch (err) {
      alert("Error contacting the backend API.");
      console.error(err);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleUpgrade = async () => {
    try {
      const res = await fetch(`${API_URL}/payments/checkout`, {
        method: "POST"
      });
      if (res.ok) {
        const data = await res.json();
        // Redirect user to Stripe Checkout session url (or mock checkout url)
        if (data.checkout_url.startsWith("http")) {
          window.location.href = data.checkout_url;
        } else {
          navigateTo(data.checkout_url);
        }
      } else {
        alert("Failed to start checkout session.");
      }
    } catch (err) {
      console.error(err);
      alert("Billing connection error.");
    }
  };

  const handleDowngrade = async () => {
    if (!confirm("Are you sure you want to cancel your Pro plan subscription?")) return;
    try {
      const res = await fetch(`${API_URL}/payments/mock-downgrade`, {
        method: "POST"
      });
      if (res.ok) {
        alert("Downgraded to Free tier successfully.");
        fetchUserProfile();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isChatting || !selectedRepoId) return;

    const userMessage: Message = {
      id: Math.random().toString(),
      role: "user",
      content: query
    };

    setMessages(prev => [...prev, userMessage]);
    setQuery("");
    setIsChatting(true);

    try {
      const res = await fetch(`${API_URL}/repos/${selectedRepoId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: userMessage.content })
      });

      if (res.ok) {
        const data = await res.json();
        setMessages(prev => [...prev, {
          id: Math.random().toString(),
          role: "assistant",
          content: data.answer
        }]);
        setSources(data.sources);
        if (data.sources.length > 0) {
          setActiveSource(data.sources[0]);
        }
      } else {
        setMessages(prev => [...prev, {
          id: Math.random().toString(),
          role: "system",
          content: "Sorry, I hit an error trying to process your request."
        }]);
      }
    } catch (err) {
      console.error(err);
      setMessages(prev => [...prev, {
        id: Math.random().toString(),
        role: "system",
        content: "Network error. Failed to connect to backend."
      }]);
    } finally {
      setIsChatting(false);
    }
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-dark-900 text-slate-100 font-sans">
      
      {/* 1. Sidebar - Repository list & branding */}
      <div className="w-80 border-r border-dark-700 bg-dark-800 flex flex-col justify-between select-none">
        
        {/* Sidebar Header */}
        <div>
          <div className="p-5 border-b border-dark-700 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="p-2 bg-gradient-to-tr from-blue-600 to-indigo-600 rounded-lg text-white shadow-md">
                <Cpu className="w-6 h-6 animate-pulse" />
              </div>
              <div>
                <h1 className="font-extrabold text-lg bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-indigo-300 tracking-wide">
                  GitStage
                </h1>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="text-[10px] text-slate-500 font-semibold uppercase tracking-widest">
                    @{username}
                  </span>
                  <span className={`text-[8px] font-extrabold px-1 rounded-sm ${
                    userTier === "pro" 
                      ? "bg-indigo-500/20 text-indigo-400 border border-indigo-500/30" 
                      : "bg-slate-500/20 text-slate-400 border border-slate-500/30"
                  }`}>
                    {userTier.toUpperCase()}
                  </span>
                </div>
              </div>
            </div>
            <button 
              onClick={() => setShowConnectModal(true)}
              className="p-1.5 bg-blue-600 hover:bg-blue-500 rounded text-slate-100 font-medium transition duration-200"
              title="Connect Repository"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>

          {/* Repositories List */}
          <div className="p-4 space-y-2 overflow-y-auto max-h-[300px]">
            <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest px-2 mb-3">
              Repositories
            </h2>
            
            {repos.length === 0 ? (
              <div className="text-center p-6 bg-dark-900 bg-opacity-50 rounded-lg border border-dark-700">
                <FolderGit2 className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                <p className="text-xs text-slate-500">No synced repositories.</p>
              </div>
            ) : (
              repos.map(r => {
                const isSelected = r.id === selectedRepoId;
                return (
                  <div
                    key={r.id}
                    onClick={() => setSelectedRepoId(r.id)}
                    className={`w-full text-left p-3 rounded-lg flex items-center justify-between cursor-pointer border transition duration-200 ${
                      isSelected 
                        ? "bg-dark-700 border-blue-500 text-white shadow" 
                        : "bg-dark-950/40 border-dark-750 hover:bg-dark-700/50 hover:text-white text-slate-400"
                    }`}
                  >
                    <div className="flex items-center gap-3 overflow-hidden">
                      <FolderGit2 className={`w-5 h-5 flex-shrink-0 ${isSelected ? 'text-blue-400' : 'text-slate-500'}`} />
                      <div className="overflow-hidden">
                        <div className="text-sm font-semibold truncate leading-tight">{r.name}</div>
                        <div className="text-[10px] text-slate-500 truncate mt-0.5">{r.full_name}</div>
                      </div>
                    </div>

                    <div>
                      {r.status === "synced" && <CheckCircle2 className="w-4 h-4 text-emerald-500" />}
                      {r.status === "indexing" && <RefreshCw className="w-4 h-4 text-blue-500 animate-spin" />}
                      {r.status === "pending" && <Loader2 className="w-4 h-4 text-yellow-500 animate-spin" />}
                      {r.status === "failed" && <AlertCircle className="w-4 h-4 text-red-500" />}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* PRO Tier Upsell Block */}
        <div className="p-4 border-t border-dark-700 bg-dark-950/40 flex flex-col justify-end">
          {userTier === "free" ? (
            <div className="bg-gradient-to-br from-indigo-900/40 to-blue-900/20 border border-indigo-800/40 p-4 rounded-xl shadow-lg relative overflow-hidden">
              <div className="absolute top-0 right-0 w-20 h-20 bg-indigo-500 rounded-full blur-[40px] opacity-20 pointer-events-none"></div>
              <div className="flex items-center gap-1.5 mb-2 text-indigo-400 font-bold text-xs uppercase tracking-wider">
                <Sparkles className="w-3.5 h-3.5" />
                <span>GitStage Pro Plan</span>
              </div>
              <p className="text-[11px] text-slate-300 leading-relaxed mb-3">
                Unlock unlimited repositories, codebase-wide RAG chat, and auto PR reviews.
              </p>
              <button 
                onClick={handleUpgrade}
                className="w-full bg-indigo-600 hover:bg-indigo-500 text-white py-2 rounded-lg font-bold text-xs transition duration-200 shadow-md flex items-center justify-center gap-1.5"
              >
                <CreditCard className="w-3.5 h-3.5" /> Upgrade ($15/mo)
              </button>
            </div>
          ) : (
            <div className="bg-emerald-950/15 border border-emerald-800/20 p-4 rounded-xl shadow-sm">
              <div className="flex items-center gap-1.5 text-emerald-400 font-bold text-xs uppercase tracking-wider mb-1">
                <CheckCircle2 className="w-4 h-4" />
                <span>Pro Membership Active</span>
              </div>
              <p className="text-[10px] text-slate-400 leading-normal mb-3">
                You have access to unlimited workspaces and advanced RAG tools.
              </p>
              <button 
                onClick={handleDowngrade}
                className="text-[10px] text-red-400 hover:text-red-300 font-semibold uppercase tracking-wider self-start"
              >
                Cancel Subscription
              </button>
            </div>
          )}

          {/* Footer Server Info */}
          <div className="flex items-center justify-between text-xs text-slate-500 mt-4 pt-4 border-t border-dark-700">
            <div className="flex items-center gap-1.5 font-medium">
              <Server className="w-3.5 h-3.5 text-emerald-500" />
              <span>Status: Connected</span>
            </div>
            <span className="text-[10px] bg-dark-700 px-2 py-0.5 rounded text-blue-400 font-mono">v1.0.0</span>
          </div>
        </div>
      </div>

      {/* 2. Chat Area - Center Panel */}
      <div className="flex-1 flex flex-col min-w-0 border-r border-dark-700 bg-dark-900">
        
        {/* Repo Header */}
        <div className="h-16 px-6 border-b border-dark-700 flex items-center justify-between bg-dark-950/20">
          <div>
            {currentRepo ? (
              <>
                <div className="flex items-center gap-2">
                  <span className="font-bold text-slate-200">{currentRepo.full_name}</span>
                  <span className={`text-[10px] px-2 py-0.5 rounded font-bold uppercase tracking-wider ${
                    currentRepo.status === "synced" ? "bg-emerald-500/10 text-emerald-400" :
                    currentRepo.status === "indexing" ? "bg-blue-500/10 text-blue-400" : "bg-red-500/10 text-red-400"
                  }`}>
                    {currentRepo.status}
                  </span>
                </div>
                {currentRepo.files_count !== undefined && (
                  <p className="text-[11px] text-slate-400 font-semibold mt-0.5">
                    {currentRepo.files_count} indexed files • {currentRepo.chunks_count} code snippets
                  </p>
                )}
              </>
            ) : (
              <span className="text-slate-400 font-semibold">Select a repository to begin</span>
            )}
          </div>

          <button 
            onClick={() => selectedRepoId && fetchRepoDetails(selectedRepoId)} 
            className="p-2 hover:bg-dark-750 text-slate-400 hover:text-slate-100 rounded transition"
            title="Refresh"
            disabled={!selectedRepoId}
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>

        {/* Progress Display (if actively indexing) */}
        {syncProgress && (
          <div className="bg-blue-950 bg-opacity-25 border-b border-blue-900 p-4 animate-fade-in flex flex-col gap-2">
            <div className="flex justify-between items-center text-xs">
              <span className="font-semibold text-blue-300 flex items-center gap-2">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                {syncProgress.message}
              </span>
              <span className="font-mono text-blue-400 font-bold">{syncProgress.progress}%</span>
            </div>
            <div className="w-full bg-blue-950 rounded-full h-1.5 overflow-hidden">
              <div 
                className="bg-blue-500 h-full rounded-full transition-all duration-300"
                style={{ width: `${syncProgress.progress}%` }}
              ></div>
            </div>
          </div>
        )}

        {/* Chat Message Logs */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {messages.map(msg => (
            <div 
              key={msg.id} 
              className={`flex gap-3 max-w-[85%] ${msg.role === "user" ? "ml-auto flex-row-reverse" : ""}`}
            >
              <div className={`p-2.5 h-10 w-10 rounded-lg flex items-center justify-center flex-shrink-0 ${
                msg.role === "user" 
                  ? "bg-blue-600 text-white" 
                  : msg.role === "system" 
                    ? "bg-red-500/10 text-red-400" 
                    : "bg-dark-700 text-blue-400"
              }`}>
                {msg.role === "user" ? <MessageSquare className="w-5 h-5" /> : <Cpu className="w-5 h-5" />}
              </div>

              <div className={`p-4 rounded-xl leading-relaxed text-sm shadow ${
                msg.role === "user" 
                  ? "bg-blue-600 text-white rounded-tr-none" 
                  : "bg-dark-800 text-slate-200 border border-dark-750 rounded-tl-none"
              }`}>
                <div className="whitespace-pre-wrap select-text">{msg.content}</div>
              </div>
            </div>
          ))}
          {isChatting && (
            <div className="flex gap-3 max-w-[80%]">
              <div className="p-2.5 h-10 w-10 rounded-lg bg-dark-700 text-blue-400 flex items-center justify-center">
                <Cpu className="w-5 h-5 animate-spin" />
              </div>
              <div className="p-4 rounded-xl bg-dark-800 border border-dark-750 rounded-tl-none text-slate-400 text-sm flex items-center gap-2 font-medium">
                Searching codebase & reasoning...
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Chat input box */}
        <div className="p-4 border-t border-dark-700 bg-dark-950/20">
          <form onSubmit={handleSendMessage} className="flex gap-2">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={selectedRepoId ? "Ask a question about the code..." : "Connect a repository to chat"}
              className="flex-1 bg-dark-950/50 border border-dark-700 rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-blue-500 transition disabled:opacity-50 text-slate-200"
              disabled={!selectedRepoId || isChatting}
            />
            <button
              type="submit"
              disabled={!selectedRepoId || isChatting || !query.trim()}
              className="px-5 bg-blue-600 hover:bg-blue-500 disabled:bg-dark-750 disabled:text-slate-500 rounded-lg text-white font-medium flex items-center justify-center transition duration-200"
            >
              <Send className="w-4 h-4" />
            </button>
          </form>
        </div>
      </div>

      {/* 3. Code View - Right Panel */}
      <div className="w-[500px] xl:w-[650px] border-l border-dark-700 bg-dark-950/40 flex flex-col">
        
        {/* Panel Header */}
        <div className="h-16 px-5 border-b border-dark-700 flex items-center gap-2 bg-dark-950/20">
          <Code2 className="w-5 h-5 text-blue-400" />
          <h2 className="font-bold text-slate-200">Semantic Source Inspector</h2>
        </div>

        {/* Sources Selection tabs */}
        {sources.length > 0 ? (
          <div className="border-b border-dark-700 bg-dark-950/20 p-2 flex gap-1.5 overflow-x-auto select-none">
            {sources.map((src, idx) => {
              const isActive = activeSource === src;
              return (
                <button
                  key={idx}
                  onClick={() => setActiveSource(src)}
                  className={`px-3 py-1.5 rounded text-xs font-semibold flex items-center gap-1.5 flex-shrink-0 transition border ${
                    isActive 
                      ? "bg-blue-600/10 border-blue-500 text-blue-400" 
                      : "bg-dark-800/40 border-dark-750 hover:bg-dark-700/40 text-slate-400"
                  }`}
                >
                  <FileCode2 className="w-3.5 h-3.5" />
                  <span className="max-w-[120px] truncate">{src.filepath.split("/").pop()}</span>
                </button>
              );
            })}
          </div>
        ) : null}

        {/* Source Code Viewport */}
        <div className="flex-1 overflow-y-auto p-4 font-mono text-xs relative select-text">
          {activeSource ? (
            <div className="space-y-4">
              
              {/* Header metadata details */}
              <div className="bg-dark-800 rounded-lg p-4 border border-dark-750 shadow-sm flex items-center justify-between">
                <div>
                  <div className="text-slate-300 font-bold text-sm tracking-wide truncate">
                    {activeSource.filepath}
                  </div>
                  <div className="text-slate-500 text-xxs font-bold mt-1 uppercase flex items-center gap-2">
                    <span className="px-1.5 py-0.5 bg-dark-700 rounded text-blue-400">{activeSource.type}</span>
                    <span>Line {activeSource.start_line} - {activeSource.end_line}</span>
                  </div>
                </div>
                <div className="text-slate-400 text-xs font-bold bg-dark-700 px-2.5 py-1 rounded-md border border-dark-600">
                  {activeSource.name}
                </div>
              </div>

              {/* Code text block */}
              <div className="rounded-lg overflow-hidden border border-dark-750 shadow">
                <SyntaxHighlighter
                  language="python"
                  style={atomDark}
                  showLineNumbers={true}
                  startingLineNumber={activeSource.start_line}
                  customStyle={{
                    margin: 0,
                    background: "#0D111A",
                    padding: "16px",
                    fontFamily: "Fira Code, JetBrains Mono, source-code-pro, monospace"
                  }}
                >
                  {activeSource.code_content}
                </SyntaxHighlighter>
              </div>
            </div>
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-500 px-6 text-center select-none">
              <Code2 className="w-12 h-12 text-dark-600 mb-3" />
              <h3 className="font-semibold text-slate-400 mb-1">Code Inspector</h3>
              <p className="text-xs text-slate-600 max-w-[280px]">
                Ask questions about your codebase, and we will highlight the semantic matches here.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* 4. MODAL: Connect new local Repository */}
      {showConnectModal && (
        <div className="fixed inset-0 bg-black bg-opacity-70 flex items-center justify-center p-4 z-50 animate-fade-in backdrop-blur-sm select-none">
          <div className="bg-dark-800 border border-dark-700 w-full max-w-md rounded-xl shadow-2xl p-6">
            <h3 className="text-lg font-bold text-slate-100 flex items-center gap-2 mb-2">
              <FolderGit2 className="text-blue-500 w-5 h-5" /> Connect Codebase Directory
            </h3>
            
            {userTier === "free" && repos.length >= 1 ? (
              <div className="my-4 bg-indigo-950/20 border border-indigo-850 p-4 rounded-lg flex flex-col gap-2">
                <div className="flex items-center gap-2 text-indigo-400 font-bold text-xs">
                  <Lock className="w-4 h-4" /> REPOSITORY LIMIT REACHED
                </div>
                <p className="text-[11px] text-slate-400 leading-normal">
                  Your Free tier allows only 1 workspace. Unlock unlimited indexing with our Pro Membership!
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setShowConnectModal(false);
                    handleUpgrade();
                  }}
                  className="mt-2 text-xs bg-indigo-600 hover:bg-indigo-500 text-white font-bold py-2 rounded transition"
                >
                  Upgrade to Pro ($15/mo)
                </button>
              </div>
            ) : (
              <>
                <p className="text-xs text-slate-400 mb-5 leading-normal">
                  Provide a local directory path containing Python files. We will run AST segmentation and index the structures into your vector store.
                </p>

                <form onSubmit={handleConnectRepo} className="space-y-4">
                  <div>
                    <label className="block text-xxs font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                      Repository Name
                    </label>
                    <input
                      type="text"
                      required
                      placeholder="e.g. backend-api"
                      value={repoName}
                      onChange={(e) => setRepoName(e.target.value)}
                      className="w-full bg-white border border-dark-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 text-black placeholder:text-slate-400"
                    />
                  </div>

                  <div>
                    <label className="block text-xxs font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                      Owner Username
                    </label>
                    <input
                      type="text"
                      required
                      value={ownerUsername}
                      onChange={(e) => setOwnerUsername(e.target.value)}
                      className="w-full bg-white border border-dark-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 text-black placeholder:text-slate-400"
                    />
                  </div>

                  <div>
                    <label className="block text-xxs font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                      Local Folder Path
                    </label>
                    <input
                      type="text"
                      required
                      value={localPath}
                      onChange={(e) => setLocalPath(e.target.value)}
                      placeholder="e.g. /app"
                      className="w-full bg-white border border-dark-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 text-black placeholder:text-slate-400"
                    />
                    <span className="text-[10px] text-slate-500 mt-1 block">
                      Tip: Use <code className="bg-dark-900 px-1 py-0.5 rounded text-blue-400">/app</code> to self-index GitStage's backend code.
                    </span>
                  </div>

                  <div className="flex justify-end gap-2 pt-4 border-t border-dark-700 mt-5">
                    <button
                      type="button"
                      onClick={() => setShowConnectModal(false)}
                      className="px-4 py-2 text-xs font-semibold text-slate-400 hover:text-slate-100 hover:bg-dark-700 rounded transition"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={isSubmitting}
                      className="px-4 py-2 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded flex items-center gap-1.5 transition"
                    >
                      {isSubmitting ? (
                        <>
                          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Ingesting...
                        </>
                      ) : (
                        "Index Codebase"
                      )}
                    </button>
                  </div>
                </form>
              </>
            )}
          </div>
        </div>
      )}

    </div>
  );
}

// ==================== MOCK STRIPE CHECKOUT PAGE ====================
function MockCheckoutPage({ navigateTo }: { navigateTo: (path: string) => void }) {
  const [isProcessing, setIsProcessing] = useState(false);

  const triggerMockUpgrade = async () => {
    setIsProcessing(true);
    try {
      const res = await fetch(`${API_URL}/payments/mock-upgrade`, {
        method: "POST"
      });
      if (res.ok) {
        // Upgrade complete, simulate redirecting back to successful dashboard callback
        setTimeout(() => {
          navigateTo("/");
        }, 1500);
      } else {
        alert("Upgrade failed.");
        setIsProcessing(false);
      }
    } catch (err) {
      console.error(err);
      alert("Error contacting the backend upgrade helper.");
      setIsProcessing(false);
    }
  };

  return (
    <div className="w-screen h-screen bg-[#F8F9FA] text-[#333333] flex flex-col md:flex-row items-stretch select-none font-sans">
      
      {/* Left Summary Pane */}
      <div className="flex-1 bg-white border-b md:border-b-0 md:border-r border-[#E2E8F0] p-8 md:p-16 flex flex-col justify-between">
        <div>
          <button 
            onClick={() => navigateTo("/")}
            className="text-xs font-bold text-indigo-600 uppercase tracking-widest mb-10 hover:text-indigo-500"
          >
            ← Back to GitStage
          </button>
          
          <div className="flex items-center gap-1 text-slate-500 text-xs font-bold uppercase tracking-wider mb-2">
            <span>Subscribe to</span>
            <span className="text-indigo-600 font-extrabold">GitStage Pro</span>
          </div>
          
          <div className="flex items-baseline gap-2 mb-8">
            <span className="text-4xl font-extrabold text-slate-900">$15.00</span>
            <span className="text-slate-500 font-semibold text-sm">/ month</span>
          </div>

          <div className="space-y-4">
            <div className="flex items-start gap-3 text-sm">
              <Check className="w-4 h-4 text-emerald-500 mt-0.5 flex-shrink-0" />
              <div>
                <p className="font-semibold text-slate-800">Unlimited repositories</p>
                <p className="text-xs text-slate-500">Index as many public or private repos as you want.</p>
              </div>
            </div>
            <div className="flex items-start gap-3 text-sm">
              <Check className="w-4 h-4 text-emerald-500 mt-0.5 flex-shrink-0" />
              <div>
                <p className="font-semibold text-slate-800">Advanced codebase RAG Q&A</p>
                <p className="text-xs text-slate-500">Deep, context-aware chatbot querying across all structures.</p>
              </div>
            </div>
            <div className="flex items-start gap-3 text-sm">
              <Check className="w-4 h-4 text-emerald-500 mt-0.5 flex-shrink-0" />
              <div>
                <p className="font-semibold text-slate-800">Automatic commit hooks</p>
                <p className="text-xs text-slate-500">Auto-update search databases immediately on git pushes.</p>
              </div>
            </div>
          </div>
        </div>

        <p className="text-xs text-slate-400 mt-10">
          Powered by Stripe Sandbox • 256-bit encryption
        </p>
      </div>

      {/* Right Billing Payment Pane */}
      <div className="flex-1 bg-[#F8F9FA] p-8 md:p-16 flex flex-col justify-center max-w-xl mx-auto">
        <div className="bg-white p-6 md:p-8 rounded-xl shadow-sm border border-[#E2E8F0] space-y-6">
          <h3 className="font-bold text-lg text-slate-900">Credit Card Sandbox</h3>
          
          <div className="space-y-4">
            <div>
              <label className="block text-xxs font-bold text-slate-500 uppercase tracking-wide mb-1">
                Email Address
              </label>
              <input 
                type="text" 
                disabled 
                value="dev@gitstage.local" 
                className="w-full bg-[#F3F4F6] border border-[#CBD5E1] rounded-lg px-3 py-2 text-sm text-slate-500 focus:outline-none cursor-not-allowed" 
              />
            </div>

            <div>
              <label className="block text-xxs font-bold text-slate-500 uppercase tracking-wide mb-1">
                Card Information
              </label>
              <div className="relative">
                <input 
                  type="text" 
                  disabled 
                  value="4242  4242  4242  4242" 
                  className="w-full bg-white border border-[#CBD5E1] rounded-lg pl-3 pr-20 py-2.5 text-sm text-slate-700 font-mono focus:outline-none" 
                />
                <span className="absolute right-3 top-2.5 text-[10px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded font-bold uppercase">
                  Sandbox
                </span>
              </div>
            </div>

            <div className="flex gap-4">
              <div className="flex-1">
                <label className="block text-xxs font-bold text-slate-500 uppercase tracking-wide mb-1">
                  Expiry Date
                </label>
                <input 
                  type="text" 
                  disabled 
                  value="12 / 29" 
                  className="w-full bg-white border border-[#CBD5E1] rounded-lg px-3 py-2.5 text-sm text-slate-700 font-mono focus:outline-none" 
                />
              </div>
              <div className="flex-1">
                <label className="block text-xxs font-bold text-slate-500 uppercase tracking-wide mb-1">
                  CVC Code
                </label>
                <input 
                  type="text" 
                  disabled 
                  value="123" 
                  className="w-full bg-white border border-[#CBD5E1] rounded-lg px-3 py-2.5 text-sm text-slate-700 font-mono focus:outline-none" 
                />
              </div>
            </div>
          </div>

          <button
            onClick={triggerMockUpgrade}
            disabled={isProcessing}
            className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-400 text-white font-bold py-3.5 rounded-lg shadow transition flex items-center justify-center gap-2 text-sm mt-4"
          >
            {isProcessing ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" /> Upgrading account...
              </>
            ) : (
              "Pay $15.00 and Subscribe"
            )}
          </button>
        </div>
      </div>

    </div>
  );
}
