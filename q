[33mcommit c453afb5e1e4fcb53ed69d83b661f93fbd1a9764[m[33m ([m[1;36mHEAD[m[33m -> [m[1;32mmain[m[33m, [m[1;31morigin/main[m[33m, [m[1;31morigin/HEAD[m[33m)[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Wed Nov 11 12:31:41 2026 +0000

    mention PyPI

[33mcommit 253677bd35962042f1e6c3a3991e655868064ed6[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Wed Nov 11 12:09:13 2026 +0000

    spool-and-install temporary directories

[33mcommit b4d9b817ca291af18716ff1c2f5df860a1bf90a6[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Tue Nov 10 18:18:00 2026 +0000

    remove referencs to Pyinfra from code comments

[33mcommit 760e5154d04ae3cb03565f296dce5f1d5a2b5b5e[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Tue Nov 10 11:08:07 2026 +0000

    address implementation gaps

[33mcommit 90cf9cc654487b094fd972827c70dc773a43f08d[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Tue Nov 10 09:56:59 2026 +0000

    update .gitignore

[33mcommit 7c7a9c98dd7f608e8f437077f85bcf270c439a18[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Tue Nov 10 09:54:31 2026 +0000

    replace in-place with spool-and-insert

[33mcommit 2d81528a0710eb16aab265c4255ab1229d1b07ee[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Mon Nov 9 19:57:38 2026 +0000

    snap and flatpak

[33mcommit ac85becb8690ac737b1cb3e0d959fcfef0a9b844[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Nov 8 15:45:46 2026 +0000

    server and file operations

[33mcommit e1f8d7d0c0462cc387de1f6626ed03c0e012887a[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Nov 8 15:35:55 2026 +0000

    POC-scope wording cleanup: real transport, no POC labels
    
    README, the docs home page, the mkdocs site_description and the pyproject
    description still described reemote5 as a proof-of-concept restricted to
    generating commands. Align them with the implemented model: a
    generator-based two-phase execution library whose default
    Host.from_inventory opens a real asyncssh connection per inventory entry
    (and a DockerHost for connector "docker"), while SimulatedHost belongs to
    the test framework and is injected through run's host_factory.
    
    Also repoint the hidden API-reference table's actions link at the page that
    actually exists, docs/actions/actions.md, and reword the design-doc note so
    it no longer labels the project a POC.

[33mcommit 63a23f52c4c7b23179888d58f831a68c93a3461b[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Nov 8 10:15:55 2026 +0000

    fix examples

[33mcommit 8317efb3ce140a732d0431d1cb569935f48614c2[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sat Nov 7 17:36:50 2026 +0000

    enterprise modules sysclt, systemd ufw etc.

[33mcommit b97aa6942f7fdea528e17d9339859d05b6b89b56[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Thu Nov 5 20:11:24 2026 +0000

    consistent document formatting

[33mcommit 3d49b250595d2e7c9d060cfcff51b2089bd14789[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Thu Nov 5 12:18:19 2026 +0000

    streamlined module documentation

[33mcommit 3d21d46ace834e1cafa24e5ffbd2982f2ed8f7c2[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Tue Nov 3 08:29:53 2026 +0000

    apk examples

[33mcommit 0e846ae1bc3d6595492b947b4cb338a1d7ef60a9[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Tue Nov 3 06:37:52 2026 +0000

    git facts and operations

[33mcommit 5d9bfb255a60a830ab5b0e0c114846651b1ed063[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Mon Nov 2 18:18:00 2026 +0000

    privilege doas

[33mcommit 09914b554d48c57f8461f1fa24d724c0f8e70891[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Mon Nov 2 16:29:15 2026 +0000

    apk operations and facts

[33mcommit e4de0435809563f018556f077ed81b5ced0c1037[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Mon Nov 2 11:52:07 2026 +0000

    add SPDX-License-Identifier: EPL-2.0

[33mcommit 504f1db2911f8803c3f9c12a4477336ff2d1c600[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Mon Nov 2 11:45:50 2026 +0000

    apk facts

[33mcommit 4001a0d4ce25e2651bf1f083063ffd5a742f0a00[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Mon Nov 2 10:07:18 2026 +0000

    improve dnf apt consistency

[33mcommit 6743a8727eb46db6e6da5f606aac50530f23a23c[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Mon Nov 2 08:45:40 2026 +0000

    apt operations

[33mcommit 1779feb2130bf00110fba1c41f1b3432b70f2504[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Nov 1 12:43:08 2026 +0000

    action shell

[33mcommit 159c292772053d62f7f7f7d00fd158975052ed32[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Nov 1 12:17:23 2026 +0000

    when

[33mcommit 11ab666e6945730732590b1270055e9ae7cf168d[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Nov 1 11:22:18 2026 +0000

    server operations

[33mcommit 5c35cf995394c36315f1ea94e974e4551e08faca[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sat Oct 31 13:05:07 2026 +0000

    docker connector

[33mcommit 85bb13984bd9499c32de06be9236cc1088b7ddda[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sat Oct 31 08:38:57 2026 +0000

    file facts

[33mcommit 853ba5603ac478def85a8bd51b7dce83ee20cc8a[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sat Oct 31 07:04:04 2026 +0000

    hardware facts

[33mcommit 6b1e5343752bd983cf4be01d73ebee7b4149f8e3[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sat Oct 31 04:25:34 2026 +0000

    server facts

[33mcommit 5f10a9744f819ff259d1258a4107c3991efd5102[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Fri Oct 30 15:40:02 2026 +0000

    dnf.update dnf.key dnf.module

[33mcommit 9180fee11dce2218658b67da5564ec22631e8c06[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Thu Oct 29 13:37:40 2026 +0000

    dnf.rpm

[33mcommit 69b8984bba8e7d399f324a50a6d12afe13118bbc[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Thu Oct 29 09:40:02 2026 +0000

    server.service server.wait_service

[33mcommit 057541ca6e4b33b44cb1414787d7826821251a30[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Thu Oct 29 08:57:03 2026 +0000

    scp file get and put

[33mcommit da81548af5ba5e9d1686ec15dd050a82e6c96436[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Wed Oct 28 14:04:03 2026 +0000

    groups tags Pivilege

[33mcommit 1d68ea34d94e1d34a1c6787789cdc879d64fd1d8[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Tue Oct 27 22:27:59 2026 +0000

    su

[33mcommit 520fce56234e215dcd4b3a98277dedf77f82ab5a[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Tue Oct 27 20:04:39 2026 +0000

    sudo passwords

[33mcommit b83e3f183d7729fb4d627869e29282d9d5fecb70[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Mon Oct 26 16:25:53 2026 +0000

    composable

[33mcommit 0518879d5936adada4315293e8a9be23d2ecb6be[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Oct 25 20:35:03 2026 +0000

    gather_fact driver-level gather helper

[33mcommit 57cef6430b8f85d603a8e7b1db464298426c2107[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Oct 25 19:53:37 2026 +0000

    fact dnf.DnfDisabledModules

[33mcommit 2e761de7c8ffd58b572be8b8669b224899e224fd[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Oct 25 19:25:11 2026 +0000

    fact dnf.DnfEnabledModules

[33mcommit b1c4abf3638de03e55aa8de92fd6342ea54470fe[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Oct 25 16:48:41 2026 +0000

    inventory ssh options

[33mcommit f3b4f97885b79d78bd75596eff56d4282fe33207[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Oct 25 16:06:59 2026 +0000

    pydanic inventory validation

[33mcommit d1b9ae04e7504d63f3dd9e47e4953f34b114836b[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Oct 25 14:46:56 2026 +0000

    switch apt to dnf

[33mcommit 41dfb93635f9174f9c8ba562511a8469131270e8[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Oct 25 12:18:35 2026 +0000

    refactor facts and operations

[33mcommit 50fb18a66acadddf3e48e664a7043db9e0249d3d[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Oct 25 08:55:12 2026 +0000

    async

[33mcommit 8df07b3002a5b03c7303db926a99a47a47da0eee[m
Author: kimjarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Oct 25 07:10:03 2026 +0000

    initial

[33mcommit 4e6817e0f5c6f874ead7f9f96f80f26b80582ed1[m
Author: Kim Jarvis <kim.jarvis@tpfsystems.com>
Date:   Sun Oct 25 05:16:58 2026 +0000

    Initial commit
