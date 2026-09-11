import { BotForm } from "@/components/bot-form";

export default function NewBotPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">New bot</h1>
        <p className="mt-0.5 text-sm text-muted">
          Set the target basket and the rule that decides when to trade back to it.
        </p>
      </div>
      <BotForm />
    </div>
  );
}
