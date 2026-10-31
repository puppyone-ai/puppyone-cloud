/** What the task user may reach. Anything but an explicit 'open' stays sealed. */
const PRIVATE_V4 = [
  '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '169.254.0.0/16', '172.16.0.0/12',
  '192.168.0.0/16', '224.0.0.0/4', '240.0.0.0/4',
];
// fc00::/7 holds Fly's private network, its resolver and its Machines API.
const PRIVATE_V6 = ['fc00::/7', 'fe80::/10', 'ff00::/8'];
const REJECT = 'counter reject with icmpx type admin-prohibited';

export const PUBLIC_DNS = 'nameserver 1.1.1.1\nnameserver 8.8.8.8\nnameserver 2606:4700:4700::1111\n';

export function egressRules(internet) {
  const closing = internet === 'open'
    ? [
        `meta skuid 1000 ip daddr { ${PRIVATE_V4.join(', ')} } ${REJECT}`,
        `meta skuid 1000 ip6 daddr { ${PRIVATE_V6.join(', ')} } ${REJECT}`,
      ]
    : [`meta skuid 1000 ${REJECT}`];
  return `table inet puppyone_task_egress {
    chain output {
      type filter hook output priority 0; policy accept;
      meta skuid 1000 ip daddr 127.0.0.0/8 accept
      meta skuid 1000 ip6 daddr ::1/128 accept
      ${closing.join('\n      ')}
    }
  }`;
}
