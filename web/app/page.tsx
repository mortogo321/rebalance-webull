import { redirect } from "next/navigation";

/** The proxy has already decided whether this request is authenticated; by the
 *  time this renders, the only sensible destination is the dashboard. */
export default function Home() {
  redirect("/dashboard");
}
