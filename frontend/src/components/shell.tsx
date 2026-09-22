"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowUpRight, BriefcaseBusiness, ChartNoAxesGantt, LayoutDashboard, Plus, Settings2, Sparkles, Workflow, PanelLeftClose, Menu } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const navigation = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/workflow", label: "Workflow", icon: Workflow },
  { href: "/applications", label: "Applications", icon: BriefcaseBusiness },
  { href: "/tracker", label: "Application Tracker", icon: ChartNoAxesGantt },
];
export function Shell({children}: {children: React.ReactNode}) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const current = [...navigation, {href: "/settings", label: "Settings"}].find(item => item.href === "/" ? pathname === "/" : pathname.startsWith(item.href));
  return <div className="min-h-screen">
    {open && <button className="fixed inset-0 z-30 bg-black/20 lg:hidden" aria-label="Close navigation" onClick={() => setOpen(false)} />}
    <aside className={cn("fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r bg-[#f8f8fa] px-4 py-7 transition-transform lg:translate-x-0", !open && "-translate-x-full")}>
      <Link href="/" className="mb-9 flex items-center gap-2.5 px-3 text-xl font-semibold tracking-tight" onClick={() => setOpen(false)}><span className="flex size-8 items-center justify-center rounded-lg bg-primary text-white"><Sparkles size={18}/></span>Career<span className="-ml-2 text-primary">AI</span></Link>
      <Button asChild className="mx-1 mb-8"><Link href="/workflow" onClick={() => setOpen(false)}><Plus/>New application</Link></Button>
      <div className="px-3 pb-3 text-[10px] font-semibold uppercase tracking-[.16em] text-muted-foreground">Workspace</div>
      <nav className="space-y-1">{navigation.map(item => <Link key={item.href} href={item.href} onClick={() => setOpen(false)} className={cn("flex items-center gap-3 rounded-lg px-3 py-2.5 text-[13px] font-medium text-muted-foreground transition-colors hover:bg-black/4 hover:text-foreground", current?.href === item.href && "bg-[#ece9f8] text-primary hover:bg-[#ece9f8]")}><item.icon size={17}/>{item.label}</Link>)}</nav>
      <div className="mt-auto"><div className="mx-1 mb-6 rounded-xl border bg-white p-4"><p className="text-xs font-medium">A thoughtful next step.</p><p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">Find the right role. Make your experience count.</p><Link href="/workflow" className="mt-3 flex items-center gap-1 text-xs font-medium text-primary">Start your workflow <ArrowUpRight size={13}/></Link></div>
      <Link href="/settings" onClick={() => setOpen(false)} className={cn("flex items-center gap-3 rounded-lg px-3 py-2.5 text-[13px] text-muted-foreground hover:bg-black/4", pathname === "/settings" && "bg-[#ece9f8] text-primary")}><Settings2 size={17}/>Settings</Link>
      <div className="mt-5 flex items-center gap-3 border-t px-2 pt-5"><span className="flex size-8 items-center justify-center rounded-full bg-[#e7e4f2] text-xs font-semibold text-primary">ME</span><div><p className="text-xs font-medium">My workspace</p><p className="mt-0.5 text-[11px] text-muted-foreground">Personal career space</p></div><PanelLeftClose className="ml-auto text-muted-foreground" size={15}/></div></div>
    </aside>
    <div className="lg:pl-60"><header className="flex h-16 items-center justify-between border-b px-5 sm:px-9"><div className="flex items-center gap-3 text-xs"><button aria-label="Open navigation" className="lg:hidden" onClick={() => setOpen(true)}><Menu size={19}/></button><span className="text-muted-foreground">Workspace</span><span className="text-muted-foreground/40">/</span><span>{current?.label || "Workflow"}</span></div><span className="flex items-center gap-2 text-[11px] text-muted-foreground"><span className="size-1.5 rounded-full bg-primary/60"/>Personal workspace</span></header><main className="mx-auto max-w-[1500px] px-5 py-9 sm:px-9 lg:px-10">{children}</main></div>
  </div>;
}
