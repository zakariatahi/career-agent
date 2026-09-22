"use client";
import { useState } from "react";
import { Check, Columns2, ListFilter, Pencil, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { Draft, Workflow } from "@/lib/api";

// A readable text preview, not a LaTeX renderer. The compiled PDF remains the final artifact.
export function readableLatex(source: string): string {
  let text = source.includes("\\begin{document}") ? source.split("\\begin{document}")[1] : source;
  text = text.replace(/(?<!\\)%[^\n]*/g, "")
    .replace(/\\begin\{tabularx\}[^\n]*/g, "")
    .replace(/\\begin\{minipage\}(?:\[[^\]]*\])?\{[^}]*\}/g, "")
    .replace(/\\(?:begin|end)\{[^}]*\}(?:\[[^\]]*\])?/g, "")
    .replace(/\\href\{[^}]*\}/g, "")
    .replace(/\\(?:fontsize|setlength)\{[^}]*\}\{[^}]*\}/g, "")
    .replace(/\\(?:color|vspace|hspace)\{[^}]*\}/g, "")
    .replace(/\\hrule\s*height\s*[\d.]+pt/g, "")
    .replace(/\\(?:cvsection|section)\{([^}]*)\}/g, "\n\n$1\n────────────────────────\n")
    .replace(/\\item\s*/g, "\n• ")
    .replace(/\\\\(?:\[[^\]]*\])?/g, "\n")
    .replace(/\\par\b/g, "\n")
    .replace(/\\[a-zA-Z]+\*?/g, "")
    .replace(/\\([&%_$#])/g, "$1")
    .replace(/[{}]/g, "")
    .replace(/(?<!\\)&/g, " · ")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/\n{3,}/g, "\n\n");
  return text.trim();
}

export function CVReview({flow, draft, edit}: {flow: Workflow; draft: Draft; edit: (changes: Partial<Draft>) => void}) {
  const [editing, setEditing] = useState<number | null>(null);
  const changes = flow.changes || [];
  const approved = draft.approved_indices || [];
  const onlyChanges = draft.cv_view === "changes";
  const toggle = (index: number) => edit({ approved_indices: approved.includes(index) ? approved.filter(i => i !== index) : [...approved, index] });
  const rows: {original: string; index?: number}[] = [];
  let cursor = 0;
  const ordered = changes.map((change, index) => ({...change, index, position: flow.cv_source.indexOf(change.original_text)})).sort((a,b) => a.position - b.position);
  for (const change of ordered) {
    if (change.position < cursor || !change.original_text) continue;
    if (change.position > cursor) rows.push({original: flow.cv_source.slice(cursor, change.position)});
    rows.push({original: change.original_text, index: change.index});
    cursor = change.position + change.original_text.length;
  }
  rows.push({original: flow.cv_source.slice(cursor)});
  const visible = onlyChanges ? changes.map((change,index) => ({original: change.original_text, index})) : rows;
  return <div>
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2 text-xs text-muted-foreground"><span className="size-2 rounded-sm bg-green-200"/>{approved.length} of {changes.length} changes selected</div><div className="flex rounded-lg border bg-muted p-1" role="group" aria-label="CV view"><Button size="sm" variant="ghost" className={!onlyChanges ? "bg-white shadow-xs" : ""} onClick={() => edit({cv_view: "side"})} aria-pressed={!onlyChanges}><Columns2/>Side-by-Side</Button><Button size="sm" variant="ghost" className={onlyChanges ? "bg-white shadow-xs" : ""} onClick={() => edit({cv_view: "changes"})} aria-pressed={onlyChanges}><ListFilter/>Changes-Only</Button></div></div>
    <div className="overflow-hidden rounded-xl border bg-white"><div className="grid grid-cols-2 border-b bg-muted/60"><div className="border-r px-5 py-4"><p className="text-sm font-semibold">Original CV</p><p className="mt-1 text-xs text-muted-foreground">Your starting point</p></div><div className="px-5 py-4"><p className="flex items-center gap-2 text-sm font-semibold">Tailored CV <span className="rounded bg-green-100 px-1.5 py-0.5 text-[9px] font-medium text-green-700">REVIEW</span></p><p className="mt-1 text-xs text-muted-foreground">Aligned with this opportunity</p></div></div>
      <div className="max-h-[680px] overflow-y-auto py-5">{visible.map((row,key) => {
        const i = row.index;
        const change = i !== undefined ? changes[i] : undefined;
        const selected = i !== undefined && approved.includes(i);
        const original = readableLatex(row.original);
        if (!original && !change) return null;
        return <div className="grid grid-cols-2" key={key}><div className="min-w-0 border-r px-5 py-2 sm:px-8"><div className="document-text">{original}</div></div><div className="min-w-0 px-4 py-2 sm:px-6"><div className={cn("rounded-md p-2", selected && "bg-[#edf8ef] ring-1 ring-green-100")}>
          {change && i !== undefined && editing === i ? <Textarea aria-label={`Edit change ${i+1}`} className="min-h-32 bg-white font-mono text-xs" value={draft.edited_changes?.[String(i)] ?? change.proposed_text} onChange={e => edit({edited_changes: {...draft.edited_changes, [String(i)]: e.target.value}})}/> : <div className="document-text">{readableLatex(selected && change ? draft.edited_changes?.[String(i)] ?? change.proposed_text : row.original)}</div>}
          {change && i !== undefined && <div className="mt-3 border-t border-green-200/60 pt-2"><p className="mb-2 text-[11px] leading-relaxed text-muted-foreground">{change.reason}</p><div className="flex flex-wrap items-center gap-1"><Button variant="ghost" size="sm" onClick={() => setEditing(editing === i ? null : i)} disabled={!selected}>{editing === i ? <Check/> : <Pencil/>}{editing === i ? "Done" : "Edit source"}</Button><Button variant="ghost" size="sm" onClick={() => toggle(i)} aria-label={`${selected ? "Revert" : "Restore"} change ${i+1}`}><RotateCcw/>{selected ? "Revert" : "Restore"}</Button></div></div>}
        </div></div></div>;
      })}{changes.length === 0 && onlyChanges && <p className="p-10 text-center text-sm text-muted-foreground">No changes proposed. Your original CV will be used.</p>}</div>
    </div><p className="mt-3 text-[11px] text-muted-foreground">Text preview · Edits retain your CV’s LaTeX formatting. Review the compiled PDF in the next step.</p>
  </div>;
}
