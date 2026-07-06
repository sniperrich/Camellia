<?php
declare(strict_types=1);

require_once __DIR__ . '/lib/common.php';

init_common_headers();

respond(200, [
    'crcSalt' => 'E77652A5A6FE19810998B02347F2D805',
]);
