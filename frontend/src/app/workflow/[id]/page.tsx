"use client";
import { useParams } from "next/navigation";
import { WorkflowScreen } from "@/components/workflow-screen";
export default function Page() { const {id} = useParams<{id:string}>(); return <WorkflowScreen key={id} id={id}/>; }
