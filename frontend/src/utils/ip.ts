// RFC 1918 private ranges, plus loopback/link-local/unspecified — anything
// an external IP-intelligence or reputation provider has nothing useful to
// say about, so it's not worth offering a lookup link for.
const PRIVATE_RANGES: Array<[number, number]> = [
  [ipToInt('10.0.0.0'),     ipToInt('10.255.255.255')],
  [ipToInt('172.16.0.0'),   ipToInt('172.31.255.255')],
  [ipToInt('192.168.0.0'),  ipToInt('192.168.255.255')],
  [ipToInt('127.0.0.0'),    ipToInt('127.255.255.255')],
  [ipToInt('169.254.0.0'),  ipToInt('169.254.255.255')],
  [ipToInt('0.0.0.0'),      ipToInt('0.255.255.255')],
]

function ipToInt(ip: string): number {
  const parts = ip.split('.').map(Number)
  if (parts.length !== 4 || parts.some(p => Number.isNaN(p) || p < 0 || p > 255)) return NaN
  return ((parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]) >>> 0
}

export function isPrivateIp(ip: string): boolean {
  const n = ipToInt(ip)
  if (Number.isNaN(n)) return true // not a plain IPv4 address — don't offer a lookup link
  return PRIVATE_RANGES.some(([lo, hi]) => n >= lo && n <= hi)
}

// True only for well-formed IPv4 addresses — used to gate the internal
// (pktIPAM) lookup link, since isPrivateIp() above also returns true for
// garbage input where there's nothing to look up.
export function isValidIpv4(ip: string): boolean {
  return !Number.isNaN(ipToInt(ip))
}
