export interface PrivateAgentSidecarConfig {
  baseUrl?: string;
  allowMutations?: boolean;
  allowRemote?: boolean;
  requestTimeoutMs?: number;
}
export declare const name: string;
export declare const inject: readonly ["tools"];
export declare const Config: unknown;
export declare function normalizeBaseUrl(raw: string, allowRemote?: boolean): string;
export declare function apply(ctx: unknown, config: PrivateAgentSidecarConfig): void;
