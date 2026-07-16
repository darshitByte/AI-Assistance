# In-chat checkout — design

**Date:** 2026-07-16
**Status:** approved (pending spec review)

## Goal

Turn checkout into a conversational flow that happens **inside the chat thread**:
after the user taps "Proceed to checkout", the assistant shows the user's saved
delivery addresses (with an "Add New Address" option), then — once an address is
chosen — the real Magento payment methods. Picking a method places the order and
drops the existing order-confirmation card into the chat.

This replaces the current slide-out `CheckoutPanel` drawer (single address form +
radio payment list) with in-chat message cards, matching the reference mockups.

## Non-goals (YAGNI — add only if asked later)

- Editing or deleting saved addresses (only add + select).
- Payment subtitles like "Visa, Mastercard, RuPay" — real Magento methods don't
  provide these; cards show the method title only.
- Persisting pickers across a page refresh — restored history stays text-only,
  same as product cards today.
- Guest checkout (store disallows it; unchanged).

## Flow

All steps render as messages in the chat thread. While a picker is awaiting a
choice, the composer is disabled and shows the placeholder
"Use the buttons above to continue…".

1. **Start.** User taps **Proceed to checkout** (from the cart panel or the
   post-add "Proceed to checkout" button). Frontend calls `GET /checkout/addresses`
   and appends an `addressPicker` bot message rendering one card per saved address
   (label + auto icon) plus a **➕ Add New Address** button.
2. **Address chosen.** Tapping a card appends a user bubble `Deliver to: {label}`,
   calls `POST /checkout/quote` with that address, and appends a `paymentPicker`
   bot message: one card per Magento payment method (title + generic icon by code)
   plus the order total. If the quote fails (empty cart, no shipping method), show
   the error text as a bot message and re-enable the composer.
3. **Payment chosen.** Tapping a method calls `POST /checkout/place`, and on success
   appends the existing `order` (OrderCard) success message; the cart is emptied.
   On failure, show the error and keep the payment picker active.
4. **Add New Address.** Tapping the button appends an inline address-form card
   (label field + the 7 existing fields). Submitting calls `POST /checkout/addresses`,
   which appends the address to the user's Mongo list and returns the updated list;
   the frontend re-renders the address picker with the new address available.

Once a picker resolves, its `done` flag disables its buttons so past pickers in the
thread can't be re-triggered.

## Icon mapping

Label → icon, computed client-side (case-insensitive substring):
`home → 🏠`, `office`/`work → 🏢`, else `📍`.
Payment code → icon, small lookup with a generic `💳` fallback.

## Backend

### `db/users.py`
- `get_addresses(username) -> list[dict]` — returns the `addresses` list. Falls back
  to `[legacy_address]` when a user has only the old single `address` field, so
  existing users don't break.
- `add_address(username, address)` — `$push` to `addresses`, bump `updated_at`.
- Keep `get_address` / `set_address` unchanged — they now represent the *selected*
  address that `checkout.place()` bills against. `quote` continues to call
  `set_address` with the chosen address.

### `api/checkout.py`
- `AddressRequest` gains `label: str = ""`.
- `GET /checkout/addresses` → `users_db.get_addresses(user)`.
- `POST /checkout/addresses` (body: `AddressRequest`) → `users_db.add_address(...)`,
  return the updated list.
- `POST /checkout/quote` and `POST /checkout/place` unchanged.
- The old `GET /checkout/address` (single) can stay or be removed once the frontend
  stops calling it — remove it since the drawer that used it is retired.

### `commerce/checkout.py`
- Unchanged. Payment methods already come from the Magento `shipping-information`
  response; `quote` returns them verbatim.

## Frontend (`frontend/src/App.jsx`)

- **Remove** the `CheckoutPanel` component, the `checkoutOpen` state, and the
  `/checkout/address` fetch. `onCheckout` now starts the in-chat flow instead of
  opening the drawer.
- **New state:** `pending` (`null | "address" | "payment"`) — drives the disabled
  composer + placeholder swap.
- **New message kinds** handled in `Message`:
  - `addressPicker` — `{ addresses, done }` → renders `AddressCard`s + Add New.
  - `paymentPicker` — `{ methods, total, done }` → renders `PaymentCard`s.
  - `addressForm` — inline new-address form.
  - `order` — reuse existing `OrderCard`.
- **New handlers in `App`:** `startCheckout()` (fetch addresses → append picker),
  `chooseAddress(addr)` (quote → append payment picker), `choosePayment(code)`
  (place → append order), `addAddress(form)` (POST → re-render picker). Each marks
  the resolved picker `done` and updates `pending`.
- **New sub-components:** `AddressCard`, `PaymentCard`, `AddressForm`. Icon helpers
  are tiny lookups.
- **CSS:** add card styles for address/payment pickers in `index.css`, reusing the
  existing card/order visual language.

## Error handling

- Quote/place failures surface as a plain bot message with the backend `error`
  string; the composer re-enables so the user isn't stuck.
- Expired Magento customer token is already retried once in `commerce/cart.py`
  (`_call`), covering quote/place.
- Empty cart at quote time returns `{ok: false, error: "Your cart is empty."}` —
  shown as a bot message.

## Testing / verification

- `commerce/checkout.py` keeps its network-free `__main__` self-check.
- Add a network-free self-check for the `users.py` address-list fallback
  (legacy single `address` → one-element list) — assert-based, no framework.
- Manual end-to-end drive (per CLAUDE.md): add item → checkout → pick/add address
  → pick real payment method → order card + invoice, via the running app.
