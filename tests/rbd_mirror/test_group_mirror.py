"""
Module to verify :
  -  Verify that a user can successfully mirror rbd group containing rbd images between primary and secondary sites
            Option 1: pool1/image1 & pool1/image2 (Images without namespaces)

Test case covered:
CEPH-83610860 - Verify that a user can successfully replicate grouped RBD images between primary and secondary sites

Pre-requisites :
1. Cluster must be up in 8.1 and above and running with capacity to create pool
2. We need atleast one client node with ceph-common package,
   conf and keyring files

Test Case Flow:
Step 1: Deploy Two ceph cluster on version 8.1 or above
Step 2: Create RBD pool ‘pool_1’ on both sites
        site-a: ceph osd pool create pool_1
                ceph osd pool application enable pool_1 rbd
                rbd pool init -p pool_1
        site-b: ceph osd pool create pool_1
                ceph osd pool application enable pool_1 rbd
                rbd pool init -p pool_1	‘pool_1’ created successfully
Step 3: Enable Image mode mirroring on pool_1 on both sites
        site-a: rbd mirror pool enable pool_1 image
        site-b: rbd mirror pool enable pool_1 image
Step 4: Bootstrap the storage cluster peers (Two-way)
        site-a: rbd mirror pool peer bootstrap create --site-name site-a pool_1 > /root/bootstrap_token_site-a
        site-b: rbd mirror pool peer bootstrap import --site-name site-b --direction rx-tx pool_1
        /root/bootstrap_token_site-a
Step 5: Create 2 RBD images in pool_1
        site-a: rbd create image1 --size 1G --pool pool_1
                rbd create image2 --size 1G --pool pool_1
Step 6: Create Consistency group
        rbd group create --pool pool_1 --group group_1
Step 7: Add Images in the consistency group
        rbd group image add --image image1 --group group_1 --group-pool pool_1 --image-pool pool_1
        rbd group image add --image image2 --group group_1 --group-pool pool_1 --image-pool pool_1
Step 8: Add data to the images
        rbd bench --io-type write --io-threads 16 --io-total 500M pool_1/image1
        rbd bench --io-type write --io-threads 16 --io-total 500M pool_1/image2
Step 9: Enable Mirroring for the group
        rbd mirror group enable --group group_1 --pool pool_1
Step 10: Wait for mirroring to complete
        rbd mirror group status pool_1/group_1
Step 11: Check all image is replicated on site-b
       Site-b:  rbd ls pool_1
Step 12: Validate size of each image should be same on site-a and site-b
        Site-a: rbd du pool_1/image1
                rbd du pool_1/image2
        Site-b: rbd du pool_1/image1
                rbd du pool_1/image2
Step 13: Check group is replicated on site-b
        Site-b: rbd group info --group group_1 --pool pool_1
Step 14: Check whether images are part of correct group on site-b
       Site-b: rbd group image list --pool pool_1 --group group_1
Step 15: Check pool mirror status, image mirror status and group mirror status on both sites does
         not show 'unknown' or 'error' on both clusters
        Site-a: Pool Level: rbd mirror pool status pool_1
                Image Level: rbd mirror image status pool_1/image1
                            rbd mirror image status pool_1/image2
                Group Level: rbd mirror group status pool_1/group_1

        Site-b: Pool Level: rbd mirror pool status pool_1
                Image Level: rbd mirror image status pool_1/image1
                            rbd mirror image status pool_1/image2
                Group Level: rbd mirror group status pool_1/group_1
Step 16: Confirm that the global ids match for the groups and images on both clusters.
        Site-a: rbd mirror group status pool_1/group_1
        Site-b: rbd mirror group status pool_1/group_1
Step 17: Validate the integrity of the data on secondary site-b
        Site-b:rbd export pool_1/image1 file1_b.txt
                rbd export pool_1/image2 file2_b.txt
                md5sum file1_b.txt
                md5sum file2_b.txt
        Note: (Md5sum should match with site-a images)
Step 18: Repeat Above steps for namespace level rbd images
Step 19: Repeat above on EC pool

"""

import json
from copy import deepcopy

from ceph.rbd.initial_config import initial_mirror_config
from ceph.rbd.mirror_utils import (
    check_mirror_consistency,
    compare_image_size_primary_secondary,
    run_IO,
)
from ceph.rbd.utils import getdict
from ceph.rbd.workflows.cleanup import cleanup
from ceph.rbd.workflows.group_mirror import (
    enable_group_mirroring_and_verify_state,
    group_mirror_status_verify,
    wait_for_idle,
)
from utility.log import Log

log = Log(__name__)


def test_group_mirroring(
    rbd_primary,
    rbd_secondary,
    client_primary,
    client_secondary,
    primary_cluster,
    secondary_cluster,
    pool_types,
    **kw
):
    """
    Test user can successfully mirror rbd group containing rbd images between primary and secondary sites
    Args:
        rbd_primary: RBD object of primary cluster
        rbd_secondary: RBD objevct of secondary cluster
        client_primary: client node object of primary cluster
        client_secondary: client node object of secondary cluster
        primary_cluster: Primary cluster object
        secondary_cluster: Secondary cluster object
        pool_types: Replication pool or EC pool
        **kw: any other arguments
    """

    for pool_type in pool_types:
        rbd_config = kw.get("config", {}).get(pool_type, {})
        multi_pool_config = deepcopy(getdict(rbd_config))

        for pool, pool_config in multi_pool_config.items():
            group_config = {}
            if "data_pool" in pool_config.keys():
                _ = pool_config.pop("data_pool")
            group_config.update({"pool": pool})

            for image, image_config in pool_config.items():
                if "image" in image:
                    # Add data to the images
                    err = run_IO(rbd_primary, client_primary, pool, image, **kw)
                    if err:
                        return 1

            # Get Groups in a Pool
            (group, out_err) = rbd_primary.group.list(**group_config)
            log.info("Groups present in pool " + pool + " are: " + group)
            if out_err:
                log.error("Listing Group in a pool Failed")
                return 1
            group_config.update({"group": group.strip()})

            # Get Group Mirroring Status
            (group_mirror_status, out_err) = rbd_primary.mirror.group.status(
                **group_config
            )
            if out_err:
                if "mirroring not enabled on the group" in out_err:
                    mirror_state = "Disabled"
                else:
                    log.error("Getting group mirror status failed : " + str(out_err))
                    return 1
            else:
                mirror_state = "Enabled"
            log.info("Group " + group + " mirroring state is " + mirror_state)

            # Enable Group Mirroring and Verify
            if mirror_state == "Disabled":
                out = enable_group_mirroring_and_verify_state(
                    rbd_primary, **group_config
                )
                if out:
                    log.error("Enabling group mirroring failed")
                    return 1

            # Wait for group mirroring to complete
            out = wait_for_idle(rbd_primary, **group_config)
            if out:
                log.error("Group Mirorring is not idle after 300 seconds")
                return 1

            # Validate size of each image should be same on site-a and site-b
            (group_image_list, out_err) = rbd_primary.group.image.list(
                **group_config, format="json"
            )
            res = compare_image_size_primary_secondary(
                rbd_primary, rbd_secondary, group_image_list
            )
            if res:  # res will be 0 when sizes match and 1 when not matched
                log.error(
                    "size of rbd images do not match on primary and secondary site"
                )
                return 1

            # Check group is replicated on site-b using group info
            (group_info_status, out_err) = rbd_secondary.group.info(
                **group_config, format="json"
            )
            if out_err:
                log.error("Getting group info failed : " + str(out_err))
                return 1
            else:
                if (
                    json.loads(group_info_status)["group_name"] != group_config["group"]
                    or json.loads(group_info_status)["mirroring"]["state"] != "enabled"
                    or json.loads(group_info_status)["mirroring"]["mode"] != "snapshot"
                    or json.loads(group_info_status)["mirroring"]["primary"]
                ):
                    log.error("group info is not as expected on secondary cluster")
                    return 1

            # Check whether images are part of correct group on site-b using group image-list
            (group_image_list_primary, out_err) = rbd_primary.group.image.list(
                **group_config, format="json"
            )
            (group_image_list_secondary, out_err) = rbd_secondary.group.image.list(
                **group_config, format="json"
            )
            if out_err:
                log.error("Getting group image list failed : " + str(out_err))
                return 1
            else:
                if json.loads(group_image_list_primary) != json.loads(
                    group_image_list_secondary
                ):
                    log.error(
                        "Group image list does not match for primary and secondary cluster"
                    )
                    return 1

            # Verify group mirroring status on both clusters & Match global id of both cluster
            out = group_mirror_status_verify(
                primary_cluster,
                secondary_cluster,
                rbd_primary,
                rbd_secondary,
                primary_state="up+stopped",
                secondary_state="up+replaying",
                **group_config
            )
            if out:
                log.error("Group mirroring status is not healthy")
                return 1

            # Validate the integrity of the data on secondary site-b
            err = check_mirror_consistency(
                rbd_primary,
                rbd_secondary,
                client_primary,
                client_secondary,
                group_image_list,
                **group_config
            )
            if err:
                return 1

    return 0


def run(**kw):
    """
    This test verify that a user can successfully mirror rbd group containing rbd images between primary and
    secondary sites
            Option 1: pool1/image1 & pool1/image2 (Images without namespaces) in a
            group is consistent between both clusters
    Args:
        kw: test data
    Returns:
        int: The return value. 0 for success, 1 otherwise

    """
    try:
        pool_types = ["rep_pool_config", "ec_pool_config"]
        log.info("Running Consistency Group Mirroring across two clusters")
        for grouptype in kw.get("config").get("grouptypes"):
            kw.get("config").update({"grouptype": grouptype})
            mirror_obj = initial_mirror_config(**kw)
            mirror_obj.pop("output", [])
            for val in mirror_obj.values():
                if not val.get("is_secondary", False):
                    rbd_primary = val.get("rbd")
                    client_primary = val.get("client")
                    primary_cluster = val.get("cluster")
                else:
                    rbd_secondary = val.get("rbd")
                    client_secondary = val.get("client")
                    secondary_cluster = val.get("cluster")

            pool_types = list(mirror_obj.values())[0].get("pool_types")
            rc = test_group_mirroring(
                rbd_primary,
                rbd_secondary,
                client_primary,
                client_secondary,
                primary_cluster,
                secondary_cluster,
                pool_types,
                **kw
            )
            if rc:
                log.error(
                    "Test: RBD group mirroring (snapshot mode) across two clusters failed"
                )
                return 1

    except Exception as e:
        log.error(
            "Test: RBD group mirroring (snapshot mode) across two clusters failed: "
            + str(e)
        )
        return 1

    finally:
        cleanup(pool_types=pool_types, multi_cluster_obj=mirror_obj, **kw)
        log.info("Cleanup")

    return 0
