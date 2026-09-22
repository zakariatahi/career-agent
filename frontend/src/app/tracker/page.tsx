import { Suspense } from "react";
import { TrackerScreen } from "@/components/tracker";
import { Loading } from "@/components/shared";
export default function Page() { return <Suspense fallback={<Loading/>}><TrackerScreen/></Suspense>; }
