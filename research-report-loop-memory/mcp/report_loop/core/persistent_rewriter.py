"""Long-lived isolated WorkBuddy CLI process for report rewriting."""
from __future__ import annotations
import json, queue, subprocess, tempfile, threading, time
from pathlib import Path
from typing import Any
from .workbuddy_cli import build_environment, discover_command, extract_json

class RewriterError(RuntimeError): pass

class PersistentRewriter:
    def __init__(self, *, model: str, effort: str|None=None, deadline_at: float):
        self.model=str(model or "").strip(); self.effort=str(effort or "").strip() or None
        self.deadline_at=float(deadline_at); self.process=None; self._temporary=None
        self._stderr_handle=None; self._lines=queue.Queue()
        if not self.model: raise RewriterError("host model id is required")
    def _remaining(self):
        value=self.deadline_at-time.time()
        if value<=0: raise RewriterError("Report Loop reached its 60-minute deadline")
        return value
    def start(self):
        if self.process is not None: raise RewriterError("rewriter already started")
        command=discover_command(); self._temporary=tempfile.TemporaryDirectory(prefix="report-rewriter-")
        root=Path(self._temporary.name); self._stderr_handle=(root/"stderr.log").open("w",encoding="utf-8")
        args=[*command,"-p","--input-format","stream-json","--output-format","stream-json",
              "--model",self.model,"--permission-mode","bypassPermissions","--tools","",
              "--setting-sources","","--no-session-persistence"]
        if self.effort: args += ["--effort",self.effort]
        env=build_environment(command); env["CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS"]="1"
        try:
            self.process=subprocess.Popen(args,cwd=root,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=self._stderr_handle,text=True,encoding="utf-8",errors="replace",bufsize=1)
        except OSError as exc: self.close(); raise RewriterError(f"cannot start rewriter: {exc}") from exc
        threading.Thread(target=self._read_stdout,daemon=True).start()
    def _read_stdout(self):
        try:
            for line in self.process.stdout: self._lines.put(line)
        finally: self._lines.put(None)
    def _send(self,prompt):
        if self.process is None or self.process.poll() is not None: raise RewriterError("rewriter is not running")
        message={"type":"user","message":{"role":"user","content":[{"type":"text","text":prompt}]}}
        try: self.process.stdin.write(json.dumps(message,ensure_ascii=False)+"\n"); self.process.stdin.flush()
        except (BrokenPipeError,OSError) as exc: raise RewriterError("rewriter stdin closed") from exc
        parts=[]
        while True:
            try: line=self._lines.get(timeout=self._remaining())
            except queue.Empty as exc: self.terminate(); raise RewriterError("rewriter timed out") from exc
            if line is None: raise RewriterError("rewriter exited")
            try: event=json.loads(line)
            except json.JSONDecodeError: continue
            if event.get("type")=="assistant":
                for item in (event.get("message") or {}).get("content") or []:
                    if item.get("type")=="text" and item.get("text"): parts.append(str(item["text"]))
            if event.get("type")=="result":
                text="\n".join(parts).strip() or str(event.get("result") or "").strip()
                if not text: raise RewriterError("rewriter returned empty output")
                return text
    def rewrite(self,payload:dict[str,Any])->str:
        base=str(payload.get("baseVersion") or "").strip()
        if not base: raise RewriterError("baseVersion is required")
        prompt=("You are the isolated Report Rewriter. Use only the supplied user context, writing history, "
          "best accepted report and sanitized revision brief. Never infer raw Judge context. Rewrite from the "
          "best report only. Return strict JSON: "+'{"baseVersion":"...","reportMarkdown":"...","changeSummary":["..."]}'+"\n\n"
          +json.dumps(payload,ensure_ascii=False))
        parsed=extract_json(self._send(prompt))
        if not self._valid(parsed,base):
            parsed=extract_json(self._send(f'Return only corrected JSON with baseVersion "{base}" and non-empty reportMarkdown.'))
        if not self._valid(parsed,base): raise RewriterError("rewriter returned invalid JSON twice")
        report=str(parsed["reportMarkdown"]).strip()
        if len(report.encode("utf-8"))>800000: raise RewriterError("report exceeds 800 KB")
        return report
    @staticmethod
    def _valid(value,base): return isinstance(value,dict) and str(value.get("baseVersion") or "").strip()==base and bool(str(value.get("reportMarkdown") or "").strip())
    def terminate(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=3)
    def close(self):
        if self.process is not None and self.process.stdin and not self.process.stdin.closed:
            try: self.process.stdin.close()
            except OSError: pass
        if self.process is not None and self.process.poll() is None: self.terminate()
        if self._stderr_handle: self._stderr_handle.close()
        if self._temporary: self._temporary.cleanup()
