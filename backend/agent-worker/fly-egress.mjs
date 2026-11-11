/**
 * What every process but root may reach, and what may reach the machine.
 * Anything but an explicit 'open' stays sealed.
 */
const PRIVATE_V4 = [
  '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '169.254.0.0/16', '172.16.0.0/12',
  '192.168.0.0/16', '224.0.0.0/4', '240.0.0.0/4',
];
// fc00::/7 holds Fly's private network, its resolver and its Machines API.
const PRIVATE_V6 = ['fc00::/7', 'fe80::/10', 'ff00::/8'];
const REJECT = 'counter reject with icmpx type admin-prohibited';
// Root owns the provider control connection and the firewall itself. The rule is
// "not root" rather than one task UID, so no other account is left unrestricted.
const UNPRIVILEGED = 'meta skuid != 0';

export const PUBLIC_DNS = 'nameserver 1.1.1.1\nnameserver 8.8.8.8\nnameserver 2606:4700:4700::1111\n';

export function egressRules(internet) {
  const closing = internet === 'open'
    ? [
        `${UNPRIVILEGED} ip daddr { ${PRIVATE_V4.join(', ')} } ${REJECT}`,
        `${UNPRIVILEGED} ip6 daddr { ${PRIVATE_V6.join(', ')} } ${REJECT}`,
      ]
    : [`${UNPRIVILEGED} ${REJECT}`];
  return `table inet puppyone_task_egress {
    chain input {
      # ICMP and ICMPv6 by number: the image has no /etc/protocols to resolve names.
      type filter hook input priority 0; policy drop;
      iif "lo" accept
      ct state established,related accept
      meta l4proto { 1, 58 } accept
      ip6 saddr fdaa::/16 tcp dport 22 accept
    }
    chain output {
      type filter hook output priority 0; policy accept;
      ${UNPRIVILEGED} ip daddr 127.0.0.0/8 accept
      ${UNPRIVILEGED} ip6 daddr ::1/128 accept
      ${closing.join('\n      ')}
    }
  }`;
}
