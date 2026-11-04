/*
 * sweet-rproc: bring up the Qualcomm remote processors on the Redmi Note 10 Pro
 * (sm6150, downstream 4.14 kernel) under HarmonyOS, which has none of the
 * Android vendor daemons that normally do it.
 *
 *  1. every subsystem restarts on its own (restart_level RELATED) instead of
 *     taking the whole phone down when it crashes;
 *  2. waits for the firmware partition (/vendor/firmware_mnt) and the qrtr
 *     helpers (qrtr-ns, rmtfs, tqftpserv, pd-mapper: started by init);
 *  3. boots the ADSP (audio) through the adsp-loader sysfs knob;
 *  4. boots the modem (it also runs the WLAN firmware on WCN3990) by holding
 *     /dev/subsys_modem open for as long as this process lives;
 *  5. asks the WLAN host driver to start (/dev/wlan "ON");
 *  6. mounts persist (sensor registry, WLAN MAC) and the Bluetooth firmware
 *     partition, and keeps hexagonrpcd running for the ADSP sensors PD.
 *
 * Each part only runs when the matching hmos2sweet patch dropped its marker
 * in /vendor/etc/sweet (feature_audio, feature_wifi, feature_sensors,
 * feature_bt).
 * Everything is reported to kmsg with the "sweet-rproc:" prefix.
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <sched.h>
#include <stdlib.h>
#include <sys/sysmacros.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/mount.h>
#include <signal.h>
#include <sys/wait.h>
#include <sys/types.h>
#include <unistd.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <linux/if_link.h>

static void klog(const char *fmt, ...)
{
	char buf[512];
	va_list ap;
	int n = snprintf(buf, sizeof(buf), "<6>sweet-rproc: ");
	va_start(ap, fmt);
	n += vsnprintf(buf + n, sizeof(buf) - n - 2, fmt, ap);
	va_end(ap);
	buf[n++] = '\n';
	int fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
	if (fd >= 0) {
		write(fd, buf, n);
		close(fd);
	}
}

static int write_file(const char *path, const char *val)
{
	int fd = open(path, O_WRONLY | O_CLOEXEC);
	if (fd < 0)
		return -errno;
	int r = write(fd, val, strlen(val)) < 0 ? -errno : 0;
	close(fd);
	return r;
}

static int read_file(const char *path, char *buf, size_t len)
{
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return -errno;
	ssize_t n = read(fd, buf, len - 1);
	close(fd);
	if (n < 0)
		return -errno;
	buf[n] = 0;
	while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == ' '))
		buf[--n] = 0;
	return 0;
}

static void subsys_report(const char *when)
{
	DIR *d = opendir("/sys/bus/msm_subsys/devices");
	struct dirent *de;
	char path[256], name[64], state[64];
	if (!d)
		return;
	while ((de = readdir(d)) != NULL) {
		if (de->d_name[0] == '.')
			continue;
		snprintf(path, sizeof(path), "/sys/bus/msm_subsys/devices/%s/name", de->d_name);
		if (read_file(path, name, sizeof(name)))
			continue;
		snprintf(path, sizeof(path), "/sys/bus/msm_subsys/devices/%s/state", de->d_name);
		if (read_file(path, state, sizeof(state)))
			strcpy(state, "?");
		klog("%s: %s (%s) %s", when, de->d_name, name, state);
	}
	closedir(d);
}

static void set_restart_levels(void)
{
	DIR *d = opendir("/sys/bus/msm_subsys/devices");
	struct dirent *de;
	char path[256];
	if (!d) {
		klog("no msm_subsys bus");
		return;
	}
	while ((de = readdir(d)) != NULL) {
		if (de->d_name[0] == '.')
			continue;
		snprintf(path, sizeof(path), "/sys/bus/msm_subsys/devices/%s/restart_level", de->d_name);
		int r = write_file(path, "related");
		if (r)
			r = write_file(path, "RELATED");
		if (r)
			klog("restart_level %s: %d", de->d_name, r);
	}
	closedir(d);
}

static int wait_path(const char *path, int secs)
{
	struct stat st;
	for (int i = 0; i < secs * 10; i++) {
		if (stat(path, &st) == 0)
			return 0;
		usleep(100000);
	}
	return -1;
}

static const char *find_subsys_state(const char *want, char *state, size_t len)
{
	DIR *d = opendir("/sys/bus/msm_subsys/devices");
	struct dirent *de;
	char path[256], name[64];
	state[0] = 0;
	if (!d)
		return state;
	while ((de = readdir(d)) != NULL) {
		if (de->d_name[0] == '.')
			continue;
		snprintf(path, sizeof(path), "/sys/bus/msm_subsys/devices/%s/name", de->d_name);
		if (read_file(path, name, sizeof(name)) || strcmp(name, want))
			continue;
		snprintf(path, sizeof(path), "/sys/bus/msm_subsys/devices/%s/state", de->d_name);
		read_file(path, state, len);
	}
	closedir(d);
	return state;
}

/* start a helper with its output going to the kernel log */
static pid_t spawn_argv(char *const argv[])
{
	pid_t p = fork();
	if (p == 0) {
		int k = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
		if (k >= 0) {
			dup2(k, 1);
			dup2(k, 2);
		}
		setvbuf(stdout, NULL, _IOLBF, 0);
		execv(argv[0], argv);
		_exit(127);
	}
	klog("started %s (pid %d)", argv[0], (int)p);
	return p;
}

static void spawn(const char *path)
{
	char *const argv[] = {(char *)path, NULL};
	spawn_argv(argv);
}

static int have(const char *feature)
{
	char path[128];
	snprintf(path, sizeof(path), "/vendor/etc/sweet/feature_%s", feature);
	return access(path, F_OK) == 0;
}

static void mount_part(const char *name, const char *dir, const char *type, unsigned long flags, const char *opts)
{
	char dev[128];
	struct stat a, b;
	char parent[128];
	snprintf(parent, sizeof(parent), "%s/..", dir);
	if (stat(dir, &a) == 0 && stat(parent, &b) == 0 && a.st_dev != b.st_dev) {
		/* already mounted by the init cfg (pre-init, before the service
		 * sandboxes are created, so that sandboxed hosts see it too) */
		klog("%s already mounted on %s", name, dir);
		return;
	}
	snprintf(dev, sizeof(dev), "/dev/block/by-name/%s", name);
	for (int i = 0; i < 50 && access(dev, F_OK); i++)
		usleep(100000);
	if (access(dev, F_OK)) /* venus: A/B firmware partitions (modem_a, bluetooth_a...) */
		snprintf(dev, sizeof(dev), "/dev/block/by-name/%s_a", name);
	int r = mount(dev, dir, type, flags, opts);
	klog("mount %s on %s (%s): %d (errno %d)", name, dir, type, r, r ? errno : 0);
}

static void list_dir_klog(const char *dir)
{
	DIR *d = opendir(dir);
	struct dirent *de;
	char line[400];
	size_t n = 0;
	if (!d) {
		klog("ls %s: errno %d", dir, errno);
		return;
	}
	line[0] = 0;
	while ((de = readdir(d)) != NULL) {
		if (de->d_name[0] == '.')
			continue;
		n += snprintf(line + n, sizeof(line) - n, "%s ", de->d_name);
		if (n > sizeof(line) - 40) {
			klog("ls %s: %s", dir, line);
			n = 0;
			line[0] = 0;
		}
	}
	if (n)
		klog("ls %s: %s", dir, line);
	closedir(d);
}

static void chmod_nodes(const char *dir, const char *prefix, mode_t mode)
{
	DIR *d = opendir(dir);
	struct dirent *de;
	char path[256];
	while (d && (de = readdir(d)) != NULL) {
		struct stat st;
		/* never "." or "..": chmod_nodes("/dev/snd", "") once made /dev
		 * itself 0666 and nobody could open a device node any more */
		if (de->d_name[0] == '.' || strncmp(de->d_name, prefix, strlen(prefix)))
			continue;
		snprintf(path, sizeof(path), "%s/%s", dir, de->d_name);
		if (lstat(path, &st) || !(S_ISCHR(st.st_mode) || S_ISBLK(st.st_mode)))
			continue;
		chmod(path, mode);
	}
	if (d)
		closedir(d);
}


/* ------------------------------------------------------------ mobile data
 * The IPA driver exposes rmnet_ipa0 once the modem is up. Configure its
 * QMAP pipes (what Android's netmgrd does) and create the rmnet_data0..3
 * mux devices (mux ids 1..4) that the RIL binds its data calls to. */
#define RMNET_IOCTL_EXTENDED 0x000089FD
#define RMNET_IOCTL_ADD_MUX_CHANNEL 0x0005
#define RMNET_IOCTL_SET_EGRESS_DATA_FORMAT 0x0006
#define RMNET_IOCTL_SET_INGRESS_DATA_FORMAT 0x0007
#define RMNET_IOCTL_EGRESS_FORMAT_MAP (1 << 1)
#define RMNET_IOCTL_INGRESS_FORMAT_MAP (1 << 1)
#define RMNET_IOCTL_INGRESS_FORMAT_DEAGGREGATION (1 << 2)
#define RMNET_IOCTL_INGRESS_FORMAT_DEMUXING (1 << 3)
struct rmnet_ext {
	unsigned int cmd;
	union {
		unsigned int data;
		struct {
			unsigned int mux_id;
			char name[IFNAMSIZ];
		} mux;
		struct {
			unsigned int legacy, agg_size, agg_count;
		} ingress;
		char pad[24];
	} u;
};

static int rmnet_ioctl(int fd, struct rmnet_ext *e)
{
	struct ifreq ifr;
	memset(&ifr, 0, sizeof(ifr));
	strncpy(ifr.ifr_name, "rmnet_ipa0", IFNAMSIZ - 1);
	ifr.ifr_ifru.ifru_data = (char *)e;
	return ioctl(fd, RMNET_IOCTL_EXTENDED, &ifr) < 0 ? -errno : 0;
}

static int set_up(int fd, const char *name, int mtu)
{
	struct ifreq ifr;
	memset(&ifr, 0, sizeof(ifr));
	strncpy(ifr.ifr_name, name, IFNAMSIZ - 1);
	if (mtu > 0) {
		ifr.ifr_mtu = mtu;
		ioctl(fd, SIOCSIFMTU, &ifr);
	}
	if (ioctl(fd, SIOCGIFFLAGS, &ifr) < 0)
		return -errno;
	ifr.ifr_flags |= IFF_UP;
	return ioctl(fd, SIOCSIFFLAGS, &ifr) < 0 ? -errno : 0;
}

static void nla_put(char *buf, int *off, unsigned short type, const void *data, int len)
{
	struct nlattr *a = (struct nlattr *)(buf + *off);
	a->nla_type = type;
	a->nla_len = NLA_HDRLEN + len;
	if (len)
		memcpy(buf + *off + NLA_HDRLEN, data, len);
	*off += NLA_ALIGN(a->nla_len);
}

static int rmnet_newlink(const char *name, int link, unsigned short mux)
{
	char buf[512];
	memset(buf, 0, sizeof(buf));
	struct nlmsghdr *h = (struct nlmsghdr *)buf;
	h->nlmsg_type = RTM_NEWLINK;
	h->nlmsg_flags = NLM_F_REQUEST | NLM_F_CREATE | NLM_F_EXCL | NLM_F_ACK;
	h->nlmsg_seq = mux;
	int off = NLMSG_HDRLEN;
	struct ifinfomsg *ifi = (struct ifinfomsg *)(buf + off);
	ifi->ifi_family = AF_UNSPEC;
	off += NLMSG_ALIGN(sizeof(*ifi));
	unsigned int l = link;
	nla_put(buf, &off, IFLA_LINK, &l, 4);
	nla_put(buf, &off, IFLA_IFNAME, name, strlen(name) + 1);
	int li = off;
	nla_put(buf, &off, IFLA_LINKINFO, NULL, 0);
	nla_put(buf, &off, IFLA_INFO_KIND, "rmnet", 6);
	int di = off;
	nla_put(buf, &off, IFLA_INFO_DATA, NULL, 0);
	nla_put(buf, &off, 1 /* IFLA_RMNET_MUX_ID */, &mux, 2);
	unsigned int fl[2] = {1 /* INGRESS_DEAGGREGATION */, 0xffffffff};
	nla_put(buf, &off, 2 /* IFLA_RMNET_FLAGS */, fl, 8);
	((struct nlattr *)(buf + di))->nla_len = off - di;
	((struct nlattr *)(buf + li))->nla_len = off - li;
	h->nlmsg_len = off;
	int s = socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC, NETLINK_ROUTE);
	if (s < 0)
		return -errno;
	struct sockaddr_nl sa = {.nl_family = AF_NETLINK};
	int ret = -EIO;
	if (sendto(s, buf, off, 0, (struct sockaddr *)&sa, sizeof(sa)) == off) {
		char rb[512];
		int n = recv(s, rb, sizeof(rb), 0);
		if (n >= (int)NLMSG_HDRLEN) {
			struct nlmsghdr *rh = (struct nlmsghdr *)rb;
			ret = rh->nlmsg_type == NLMSG_ERROR ? ((struct nlmsgerr *)NLMSG_DATA(rh))->error : 0;
		}
	}
	close(s);
	return ret;
}

static void setup_mobile_data(void)
{
	struct stat st;
	for (int i = 0; i < 180 && stat("/sys/class/net/rmnet_ipa0", &st); i++)
		sleep(1);
	if (stat("/sys/class/net/rmnet_ipa0", &st)) {
		klog("data: rmnet_ipa0 never appeared, no mobile data");
		return;
	}
	int fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	if (fd < 0)
		return;
	struct rmnet_ext e;
	memset(&e, 0, sizeof(e));
	e.cmd = RMNET_IOCTL_SET_INGRESS_DATA_FORMAT;
	e.u.data = RMNET_IOCTL_INGRESS_FORMAT_MAP | RMNET_IOCTL_INGRESS_FORMAT_DEAGGREGATION |
		   RMNET_IOCTL_INGRESS_FORMAT_DEMUXING;
	klog("data: rmnet_ipa0 ingress format -> %d", rmnet_ioctl(fd, &e));
	memset(&e, 0, sizeof(e));
	e.cmd = RMNET_IOCTL_SET_EGRESS_DATA_FORMAT;
	e.u.data = RMNET_IOCTL_EGRESS_FORMAT_MAP;
	klog("data: rmnet_ipa0 egress format -> %d", rmnet_ioctl(fd, &e));
	klog("data: rmnet_ipa0 up -> %d", set_up(fd, "rmnet_ipa0", 0));
	int link = if_nametoindex("rmnet_ipa0");
	for (int i = 0; i < 4; i++) {
		char name[IFNAMSIZ];
		snprintf(name, sizeof(name), "rmnet_data%d", i);
		int r = rmnet_newlink(name, link, (unsigned short)(i + 1));
		memset(&e, 0, sizeof(e));
		e.cmd = RMNET_IOCTL_ADD_MUX_CHANNEL;
		e.u.mux.mux_id = i + 1;
		strncpy(e.u.mux.name, name, IFNAMSIZ - 1);
		int m = rmnet_ioctl(fd, &e);
		klog("data: %s (mux %d) -> link %d, ipa mux %d, up %d", name, i + 1, r, m, set_up(fd, name, 1500));
	}
	close(fd);
}


/*
 * /dev/access_token_id must stay 0666 (access_token:access_token): every
 * HarmonyOS process opens it to read its own token. Sometimes (seen on a
 * first boot) it ends up unreadable: then foundation sees itself as a
 * non-system caller and refuses to install SceneBoard for the new user, and the
 * phone never leaves the boot animation. Keep it right and log who broke it.
 */
/* keep /dev searchable (it was once left 0666 by the bug fixed in chmod_nodes) */
static void dev_dir_guard(void)
{
	for (;;) {
		struct stat st;
		if (stat("/dev", &st) == 0 && (st.st_mode & 0777) != 0755) {
			klog("/dev was %o, restoring 0755", st.st_mode & 0777);
			chmod("/dev", 0755);
		}
		sleep(5);
	}
}

int main(void)
{
	char state[64];
	int r0, r = 0;
	int want_audio = have("audio"), want_wifi = have("wifi");
	int want_sensors = have("sensors"), want_bt = have("bt"), want_ril = have("ril");
	pid_t sensors = -1;
	int sensors_starts = 0;

	klog("start (audio %d, wifi %d, sensors %d, bt %d, ril %d)", want_audio, want_wifi, want_sensors, want_bt,
	     want_ril);
	if (fork() == 0) {
		dev_dir_guard();
		_exit(0);
	}
	set_restart_levels();
	subsys_report("before");

	/* the HarmonyOS fstab parser has no vfat: mount the sweet firmware
	 * ("modem") partition here (subsystem images + WLAN board data) */
	mount_part("modem", "/vendor/firmware_mnt", "vfat", MS_RDONLY | MS_NOSUID | MS_NODEV,
		   "shortname=lower,uid=0,gid=1000,dmask=0222,fmask=0333");
	if (wait_path("/vendor/firmware_mnt/image", 30))
		klog("firmware partition not mounted at /vendor/firmware_mnt: remote processors cannot boot");
	if (want_sensors || want_wifi)
		mount_part("persist", "/vendor/persist", "ext4", MS_NOSUID | MS_NODEV | MS_NOATIME, "");
	if (want_bt) {
		mount_part("bluetooth", "/vendor/bt_firmware", "vfat", MS_RDONLY | MS_NOSUID | MS_NODEV,
			   "shortname=lower,uid=0,gid=0,dmask=0222,fmask=0333");
		list_dir_klog("/vendor/bt_firmware");
		list_dir_klog("/vendor/bt_firmware/image");
		chmod("/dev/ttyHS0", 0666);
		chmod("/dev/btpower", 0666);
	}

	/* pd-mapper reads the .jsn domain maps and tqftpserv serves files from
	 * the firmware partition: start them only now that it is mounted */
	signal(SIGCHLD, SIG_DFL);
	spawn("/vendor/bin/pd-mapper");
	spawn("/vendor/bin/tqftpserv");
	/* give qrtr-ns / rmtfs / tqftpserv / pd-mapper a moment to register */
	sleep(2);

	{
		char cmdline[2048] = {0};
		read_file("/proc/cmdline", cmdline, sizeof(cmdline));
		if (strstr(cmdline, "hmos.norproc=1")) {
			klog("hmos.norproc=1: remote processors left off");
			for (;;)
				pause();
		}
	}
	if (want_audio || want_sensors) {
		r = write_file("/sys/kernel/boot_adsp/boot", "1");
		klog("adsp boot request: %d", r);
	}
	if (want_ril) {
		/* the IPA hardware (mobile data path) only starts once userspace asks
		 * the driver to load its firmware (ipa_fws, from /vendor/firmware);
		 * Android's init does the same write. Without it rmnet_ipa0 never
		 * shows up. */
		for (int i = 0; i < 10 && access("/dev/ipa", F_OK); i++)
			sleep(1);
		klog("ipa: firmware load request -> %d", write_file("/dev/ipa", "1"));
	}
	int modem = -1;
	if (want_wifi) {
		modem = open("/dev/subsys_modem", O_RDONLY | O_CLOEXEC);
		klog("modem: open /dev/subsys_modem -> %d (errno %d)", modem, modem < 0 ? errno : 0);
		for (int i = 0; i < 30; i++) {
			find_subsys_state("modem", state, sizeof(state));
			if (!strcmp(state, "ONLINE"))
				break;
			sleep(1);
		}
	}
	subsys_report("after");

	/* the ADSP sensors protection domain stalls (and the watchdog restarts the
	 * whole ADSP every ~40 s) unless a HLOS listener serves its files */
	if (want_sensors) {
		for (int i = 0; i < 20 && access("/dev/adsprpc-smd-secure", F_OK); i++)
			sleep(1);
		chmod_nodes("/dev", "adsprpc-smd", 0660);
		char *const argv[] = {"/vendor/bin/hexagonrpcd", "-f", "/dev/adsprpc-smd-secure", "-d", "adsp",
				      "-R", "/vendor/etc/sweet/hexagonrpc", "-s", NULL};
		sensors = spawn_argv(argv);
		sensors_starts = 1;
	}

	if (want_wifi) {
		/* the WLAN firmware (wlan_pd on the modem) can take a while to report
		 * ready; the host driver gives up after ~20 s, so ask again */
		for (int i = 0; i < 4; i++) {
			struct stat st;
			r = write_file("/dev/wlan", "ON");
			klog("wlan: /dev/wlan ON (try %d) -> %d", i + 1, r);
			if (r == 0 || stat("/sys/class/net/wlan0", &st) == 0)
				break;
			sleep(5);
		}
		for (int i = 0; i < 20; i++) {
			struct stat st;
			if (stat("/sys/class/net/wlan0", &st) == 0) {
				klog("wlan0 is up");
				break;
			}
			sleep(1);
		}
	}

	if (want_audio) {
		/* the ADSP registers the sound card a few seconds after it boots: open
		 * the ALSA nodes to the audio HDI host (it does not run as root) */
		for (int i = 0; i < 30 && access("/dev/snd/controlC0", F_OK); i++)
			sleep(1);
		chmod_nodes("/dev/snd", "", 0666);
		char cards[256] = "";
		read_file("/proc/asound/cards", cards, sizeof(cards));
		klog("sound cards: %s", cards[0] ? cards : "none");
	}

	/* the data path can take a while (IPA firmware, modem): set it up in a
	 * child so nothing else waits for it */
	if (want_ril && want_wifi) {
		pid_t p = fork();
		if (p == 0) {
			setup_mobile_data();
			_exit(0);
		}
	}

	/* keep the modem reference (closing /dev/subsys_modem shuts the modem
	 * down), restart hexagonrpcd if it exits (e.g. across an ADSP restart),
	 * reap the helpers */
	for (;;) {
		int status;
		pid_t p = wait(&status);
		if (p < 0) {
			if (errno == ECHILD)
				pause();
			continue;
		}
		klog("pid %d exited (status 0x%x)", (int)p, status);
		if (p == sensors && sensors_starts < 50) {
			sleep(5);
			char *const argv[] = {"/vendor/bin/hexagonrpcd", "-f", "/dev/adsprpc-smd-secure", "-d", "adsp",
					      "-R", "/vendor/etc/sweet/hexagonrpc", "-s", NULL};
			sensors = spawn_argv(argv);
			sensors_starts++;
		}
	}
	(void)modem;
	(void)r0;
	return 0;
}
