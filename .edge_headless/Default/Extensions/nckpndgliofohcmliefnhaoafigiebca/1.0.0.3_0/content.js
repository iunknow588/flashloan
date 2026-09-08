console.log("contentjs begin");

//实现当页面退出时向exe发送quit消息
window.onbeforeunload = onbeforeunload_handler;
function onbeforeunload_handler() {
	console.log("portContentToBackground.quit");
	portContentToBackground.postMessage({joke: "quit"});
}


chrome.runtime.onMessage.addListener(function(msg, sender){
	if(msg.Source != undefined && "MWIcbcBack" == msg.Source)
	{
		window.postMessage(msg.Data, '*'); 
	}
});

//---------------------------------js处理函数------------------------

console.log("contentjs end");