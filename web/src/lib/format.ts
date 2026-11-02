// Language-aware formatting lives in lib/i18n (fmtDateTime, fmtTime, fmtNumber, fmtAgo; useFmt() in components).
export const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
