// Backend API base — the API runs separately from this frontend.
// Override at build/run time with VITE_API_BASE.
export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

// Store currency (Magento base currency for this store is BHD).
export const CURRENCY = "BHD";
