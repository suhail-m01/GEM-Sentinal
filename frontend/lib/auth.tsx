"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, clearTokens, getTokens, setTokens } from "./api";
import type { User } from "./types";

/** Step 1 of login: credentials check. Either the device is trusted (tokens
 *  returned immediately) or an OTP has been dispatched to the registered
 *  mobile (otp_required: true). */
export interface LoginChallenge {
  user: User;
  otp_required: boolean;
  mobile_masked?: string | null;
  expires_in?: number;
  provider?: string;
  sms_error?: string | null;
  /** present only when no SMS gateway is configured (development convenience) */
  dev_otp?: string | null;
  access_token?: string;
  refresh_token?: string;
}

interface AuthCtx {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<LoginChallenge>;
  /** Step 2: verify the OTP (purpose login) → tokens. */
  completeLoginOtp: (email: string, otp: string, rememberDevice: boolean) => Promise<User>;
  resendLoginOtp: (email: string) => Promise<LoginChallenge>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>(null!);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const { access } = getTokens();
    if (!access) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await api<User>("/auth/me");
      setUser(me);
    } catch {
      clearTokens();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string): Promise<LoginChallenge> => {
    const data = await api<LoginChallenge>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    // Trusted device: tokens come back immediately.
    if (!data.otp_required && data.access_token) {
      setTokens(data.access_token, data.refresh_token);
      setUser(data.user);
    }
    return data;
  }, []);

  const completeLoginOtp = useCallback(async (email: string, otp: string, rememberDevice: boolean): Promise<User> => {
    const data = await api<{ user: User; access_token: string; refresh_token: string }>(
      "/auth/verify-otp",
      { method: "POST", body: JSON.stringify({ email, otp, purpose: "login", remember_device: rememberDevice }) }
    );
    setTokens(data.access_token, data.refresh_token);
    setUser(data.user);
    return data.user;
  }, []);

  const resendLoginOtp = useCallback(async (email: string): Promise<LoginChallenge> => {
    return api<LoginChallenge>("/auth/resend-otp", {
      method: "POST",
      body: JSON.stringify({ email, purpose: "login" }),
    });
  }, []);

  const logout = useCallback(() => {
    clearTokens();
    setUser(null);
    window.location.href = "/login";
  }, []);

  return (
    <Ctx.Provider value={{ user, loading, login, completeLoginOtp, resendLoginOtp, logout, refresh }}>
      {children}
    </Ctx.Provider>
  );
}

export const useAuth = () => {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
};

/** Client-side route guard: waits for auth, bounces to /login if signed out, warns on wrong role.
 *  `role` may be a single role or a behavior group (e.g. all L1-side buyer roles). */
export function Guard({ role, children }: { role: import("./types").Role | import("./types").Role[]; children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const allowed = Array.isArray(role) ? role : [role];
  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-navy-300 border-t-navy-800" />
      </div>
    );
  }
  if (!user) {
    setTimeout(() => {
      window.location.href = "/login";
    }, 0);
    return null;
  }
  if (!allowed.includes(user.role)) {
    return (
      <div className="p-6">
        <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          You are signed in as <b>{user.full_name}</b> ({user.role}), which does not have access to this area. Please sign
          in with an account that has the required role.
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
