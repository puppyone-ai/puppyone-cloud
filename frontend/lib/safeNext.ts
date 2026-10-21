export function safeNext(value: string | null | undefined): string | null {
  if (
    !value ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    /[\\\u0000-\u0020]/.test(value) ||
    /%(?:5c|0[0-9a-f]|1[0-9a-f])/i.test(value)
  )
    return null;
  const parsed = new URL(value, 'https://return.invalid');
  if (
    parsed.origin !== 'https://return.invalid' ||
    /^\/(?:login|auth)(?:\/|$)/.test(parsed.pathname)
  )
    return null;
  return value;
}
