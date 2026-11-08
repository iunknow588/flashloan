use manager_contracts::{PairKeyId, RouteKeyId, TokenKeyId};
use std::str::FromStr;

const USDG: &str = "0x1111111111111111111111111111111111111111";
const WETH: &str = "0x2222222222222222222222222222222222222222";

#[test]
fn golden_vectors_are_stable() {
    let native = TokenKeyId::new(
        "robinhood",
        4663,
        "0x0000000000000000000000000000000000000000",
    )
    .unwrap();
    let usdg = TokenKeyId::new("robinhood", 4663, USDG).unwrap();
    let weth = TokenKeyId::new("robinhood", 4663, WETH).unwrap();

    assert_eq!(
        native.as_str(),
        "robinhood:4663:0x0000000000000000000000000000000000000000"
    );
    assert_eq!(
        PairKeyId::new(4663, WETH, USDG).unwrap().as_str(),
        format!("pair:v1:4663:{USDG}:{WETH}")
    );
    let route = RouteKeyId::new(&[weth, usdg]).unwrap();
    assert_eq!(
        route.as_str(),
        format!("route:v1:4663:robinhood:4663:{WETH}:robinhood:4663:{USDG}")
    );
    assert_eq!(RouteKeyId::from_str(route.as_str()).unwrap(), route);
}
