import timeit
import traceback

from ceph.ceph import CommandFailed
from ceph.parallel import parallel
from tests.cephfs.cephfs_utils import FsUtils
from tests.cephfs.cephfs_utilsV1 import FsUtils as FsUtilsV1
from utility.log import Log

log = Log(__name__)


def run(ceph_cluster, **kw):
    """
    CEPH-11227
    Active-active multi MDS without any subtree pinning to particular MDS. make sure to have more than 50k directories
    and million files, Check MDS load balancing1. Check default MDS load balancing without pinning2.
    Pin 10 directories to one MDS and continue IO on other directories and check MDS load balancing,
    Check the migration of subtree to the pinned MDS3.
    Pin 50% of directories to each MDS and continue IO and check MDS load balancing

    """
    try:
        start = timeit.default_timer()
        config = kw.get("config")
        num_of_dirs = config.get("num_of_dirs")
        tc = "11227"
        dir_name = "dir"
        log.info("Running cephfs %s test case" % (tc))
        test_data = kw.get("test_data")
        fs_util_v1 = FsUtilsV1(ceph_cluster, test_data=test_data)
        erasure = (
            FsUtilsV1.get_custom_config_value(test_data, "erasure")
            if test_data
            else False
        )
        default_fs = "cephfs" if not erasure else "cephfs-ec"
        fs_util = FsUtils(ceph_cluster)
        build = config.get("build", config.get("rhbuild"))
        client_info, rc = fs_util.get_clients(build)
        if rc == 0:
            log.info("Got client info")
        else:
            raise CommandFailed("fetching client info failed")
        client1, client2, client3, client4 = ([] for _ in range(4))
        client1.append(client_info["fuse_clients"][0])
        client2.append(client_info["fuse_clients"][1])
        client3.append(client_info["kernel_clients"][0])
        client4.append(client_info["kernel_clients"][1])

        fs_details = fs_util_v1.get_fs_info(client1[0], default_fs)

        if not fs_details:
            fs_util_v1.create_fs(client1[0], default_fs)

        rc1 = fs_util_v1.auth_list(client1)
        rc2 = fs_util_v1.auth_list(client2)
        rc3 = fs_util_v1.auth_list(client3)
        rc4 = fs_util_v1.auth_list(client4)
        if rc1 == 0 and rc2 == 0 and rc3 == 0 and rc4 == 0:
            log.info("got auth keys")
        else:
            raise CommandFailed("auth list failed")

        fs_util_v1.fuse_mount(
            client1,
            client_info["mounting_dir"],
            extra_params=f" --client_fs {default_fs}",
        )
        fs_util_v1.fuse_mount(
            client2,
            client_info["mounting_dir"],
            extra_params=f" --client_fs {default_fs}",
        )

        fs_util_v1.kernel_mount(
            client3,
            client_info["mounting_dir"],
            ",".join(client_info["mon_node_ip"]),
            extra_params=f",fs={default_fs}",
        )
        fs_util_v1.kernel_mount(
            client4,
            client_info["mounting_dir"],
            ",".join(client_info["mon_node_ip"]),
            extra_params=f",fs={default_fs}",
        )

        client1[0].exec_command(sudo=True, cmd=f"ceph fs set {default_fs} max_mds 2")
        client1[0].exec_command(
            sudo=True, cmd="mkdir %s%s" % (client_info["mounting_dir"], dir_name)
        )
        with parallel() as p:
            p.spawn(
                fs_util.read_write_IO,
                client1,
                client_info["mounting_dir"],
                "g",
                "write",
            )
            p.spawn(
                fs_util.read_write_IO, client2, client_info["mounting_dir"], "g", "read"
            )
            p.spawn(
                fs_util.stress_io,
                client2,
                client_info["mounting_dir"],
                "",
                0,
                2,
                iotype="smallfile",
            )
            p.spawn(
                fs_util.stress_io,
                client3,
                client_info["mounting_dir"],
                "",
                0,
                2,
                iotype="smallfile",
            )
            p.spawn(
                fs_util.read_write_IO,
                client4,
                client_info["mounting_dir"],
                "g",
                "readwrite",
            )
            p.spawn(fs_util.read_write_IO, client3, client_info["mounting_dir"])
            for op in p:
                return_counts, rc = op

        result = fs_util.rc_verify("", return_counts)

        client1[0].exec_command(
            cmd="sudo mkdir %s%s" % (client_info["mounting_dir"], "testdir")
        )

        if result == "Data validation success":
            print("Data validation success")
            client1[0].exec_command(
                sudo=True, cmd=f"ceph fs set {default_fs} max_mds 2"
            )
            log.info("Execution of Test case CEPH-%s started:" % (tc))
            num_of_dirs = int(num_of_dirs / 5)
            with parallel() as p:
                p.spawn(
                    fs_util.mkdir_bulk,
                    client1,
                    0,
                    num_of_dirs * 1,
                    client_info["mounting_dir"] + "testdir/",
                    dir_name,
                )
                p.spawn(
                    fs_util.mkdir_bulk,
                    client2,
                    num_of_dirs * 1 + 1,
                    num_of_dirs * 2,
                    client_info["mounting_dir"] + "testdir/",
                    dir_name,
                )
                p.spawn(
                    fs_util.mkdir_bulk,
                    client1,
                    num_of_dirs * 2 + 1,
                    num_of_dirs * 3,
                    client_info["mounting_dir"] + "testdir/",
                    dir_name,
                )
                p.spawn(
                    fs_util.mkdir_bulk,
                    client2,
                    num_of_dirs * 3 + 1,
                    num_of_dirs * 4,
                    client_info["mounting_dir"] + "testdir/",
                    dir_name,
                )
                p.spawn(
                    fs_util.mkdir_bulk,
                    client1,
                    num_of_dirs * 4 + 1,
                    num_of_dirs * 5,
                    client_info["mounting_dir"] + "testdir/",
                    dir_name,
                )
                for op in p:
                    rc = op
            if rc == 0:
                log.info("Directories created successfully")
            else:
                raise CommandFailed("Directory creation failed")

            with parallel() as p:
                p.spawn(
                    fs_util.max_dir_io,
                    client1,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    0,
                    num_of_dirs * 1,
                    10,
                )
                p.spawn(
                    fs_util.max_dir_io,
                    client2,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    num_of_dirs * 1,
                    num_of_dirs * 2,
                    10,
                )
                rc = fs_util.check_mount_exists(client1[0])
                if rc == 0:
                    fs_util.pinning(
                        client1,
                        0,
                        10,
                        client_info["mounting_dir"] + "testdir/",
                        dir_name,
                        0,
                    )

                p.spawn(
                    fs_util.max_dir_io,
                    client3,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    num_of_dirs * 3,
                    num_of_dirs * 4,
                    10,
                )
                p.spawn(
                    fs_util.max_dir_io,
                    client4,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    num_of_dirs * 4,
                    num_of_dirs * 5,
                    10,
                )

            with parallel() as p:
                p.spawn(
                    fs_util.pinned_dir_io_mdsfailover,
                    client1,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    0,
                    10,
                    100,
                    fs_util.mds_fail_over,
                    client_info["clients"][0:],
                )

            with parallel() as p:
                p.spawn(
                    fs_util.pinning,
                    client2,
                    10,
                    num_of_dirs * 1,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    1,
                )
                p.spawn(
                    fs_util.pinning,
                    client3,
                    num_of_dirs * 1,
                    num_of_dirs * 2,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    1,
                )
                p.spawn(
                    fs_util.pinning,
                    client4,
                    num_of_dirs * 2,
                    num_of_dirs * 3,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    1,
                )
                p.spawn(
                    fs_util.pinning,
                    client1,
                    num_of_dirs * 3,
                    num_of_dirs * 4,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    1,
                )
                p.spawn(
                    fs_util.pinning,
                    client3,
                    num_of_dirs * 4,
                    num_of_dirs * 5,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    1,
                )

            with parallel() as p:
                p.spawn(
                    fs_util.pinned_dir_io_mdsfailover,
                    client1,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    0,
                    10,
                    100,
                    fs_util.mds_fail_over,
                    client_info["clients"][0:],
                )
            with parallel() as p:
                p.spawn(
                    fs_util.pinned_dir_io_mdsfailover,
                    client2,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    0,
                    10,
                    100,
                    fs_util.mds_fail_over,
                    client_info["clients"][0:],
                )
            with parallel() as p:
                p.spawn(
                    fs_util.pinned_dir_io_mdsfailover,
                    client3,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    0,
                    10,
                    100,
                    fs_util.mds_fail_over,
                    client_info["clients"][0:],
                )
            with parallel() as p:
                p.spawn(
                    fs_util.pinned_dir_io_mdsfailover,
                    client4,
                    client_info["mounting_dir"] + "/testdir/",
                    dir_name,
                    0,
                    10,
                    100,
                    fs_util.mds_fail_over,
                    client_info["clients"][0:],
                )

            log.info("Execution of Test case CEPH-%s ended:" % (tc))
            log.info("Cleaning up!-----")
            if client3[0].pkg_type != "deb" and client4[0].pkg_type != "deb":
                rc_client = fs_util.client_clean_up(
                    client_info["fuse_clients"],
                    client_info["kernel_clients"],
                    client_info["mounting_dir"],
                    "umount",
                )
                rc_mds = fs_util_v1.mds_cleanup(
                    client_info["clients"][0:], None, fs_name=default_fs
                )

            else:
                rc_client = fs_util.client_clean_up(
                    client_info["fuse_clients"],
                    "",
                    client_info["mounting_dir"],
                    "umount",
                )
                rc_mds = fs_util_v1.mds_cleanup(
                    client_info["clients"][0:], None, fs_name=default_fs
                )

            if rc_client == 0 and rc_mds == 0:
                log.info("Cleaning up successfull")
            else:
                return 1
        print("Script execution time:------")
        stop = timeit.default_timer()
        total_time = stop - start
        mins, secs = divmod(total_time, 60)
        hours, mins = divmod(mins, 60)
        print("Hours:%d Minutes:%d Seconds:%f" % (hours, mins, secs))
        return 0

    except CommandFailed as e:
        log.info(e)
        log.info(traceback.format_exc())
        log.info("Cleaning up!-----")
        if client3[0].pkg_type != "deb" and client4[0].pkg_type != "deb":
            rc_client = fs_util_v1.client_clean_up(
                client_info["fuse_clients"],
                client_info["kernel_clients"],
                client_info["mounting_dir"],
                "umount",
            )
            rc_mds = fs_util_v1.mds_cleanup(client_info["clients"][0:], None)

        else:
            rc_client = fs_util_v1.client_clean_up(
                client_info["fuse_clients"], "", client_info["mounting_dir"], "umount"
            )
            rc_mds = fs_util_v1.mds_cleanup(client_info["clients"][0:], None)
        if rc_client == 0 and rc_mds == 0:
            log.info("Cleaning up successfull")
        return 1
    except Exception as e:
        log.error(e)
        log.error(traceback.format_exc())
        log.info(fs_util_v1.get_fs_status_dump(client1[0]))
        fs_util_v1.get_ceph_health_status(client1[0])
        return 1
