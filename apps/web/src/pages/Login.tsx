import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Card, CardTitle } from "@/components/ui/Card";
import { FormField, Input } from "@/components/ui/Form";
import { Button } from "@/components/ui/Button";
import { login } from "@/lib/session";
import { ApiError } from "@/lib/http";

/**
 * Login uses the real server-side refresh-session flow; the access token is
 * kept in memory and the refresh credential remains an HttpOnly cookie.
 */
export function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      const from = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to reach the NOC Report Builder API.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-navy px-4">
      <Card className="w-full max-w-sm">
        <CardTitle>NOC Report Builder</CardTitle>
        <p className="mt-1 text-sm text-muted">Sign in with your operator account.</p>
        <form className="mt-6 flex flex-col gap-4" onSubmit={handleSubmit}>
          <FormField label="Username" htmlFor="username">
            <Input
              id="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </FormField>
          <FormField label="Password" htmlFor="password">
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </FormField>
          {error && <p className="text-sm text-critical">{error}</p>}
          <Button type="submit" disabled={submitting}>
            {submitting ? "Signing in..." : "Sign in"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
